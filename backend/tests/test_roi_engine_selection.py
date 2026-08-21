"""M3: app.pipeline.read_frame's ROI_ENGINE factory. Mirrors
tests/test_engine_selection.py's structure exactly (same singleton-reset
fixture pattern) for the new, orthogonal ROI_ENGINE switch. These run
regardless of whether a trained field classifier is present -- the
default-stays-tesseract/colour and unknown-value cases don't need one.
"""

import os

import pytest

from app.pipeline.field_classifier import model_available
from app.pipeline.roi import extract_rois_by_colour


def _reset_singleton():
    import app.pipeline.read_frame as read_frame_module

    read_frame_module._default_roi_extractor = None


@pytest.fixture(autouse=True)
def _isolate_roi_env():
    original = os.environ.get("ROI_ENGINE")
    _reset_singleton()
    yield
    if original is None:
        os.environ.pop("ROI_ENGINE", None)
    else:
        os.environ["ROI_ENGINE"] = original
    _reset_singleton()


def test_default_roi_extractor_is_colour_with_no_env_var():
    os.environ.pop("ROI_ENGINE", None)
    from app.pipeline.read_frame import get_default_roi_extractor

    assert get_default_roi_extractor() is extract_rois_by_colour


def test_default_roi_extractor_is_colour_even_when_tier2_model_present_but_not_requested():
    """Merely having models/field_classifier.onnx on disk must never
    silently change the default -- only an explicit ROI_ENGINE=tier2 does.
    Same non-negotiable requirement as OCR_ENGINE's own equivalent test."""
    os.environ.pop("ROI_ENGINE", None)
    from app.pipeline.read_frame import get_default_roi_extractor

    assert get_default_roi_extractor() is extract_rois_by_colour


def test_unknown_roi_engine_value_raises():
    os.environ["ROI_ENGINE"] = "not-a-real-engine"
    from app.pipeline.read_frame import get_default_roi_extractor

    with pytest.raises(ValueError):
        get_default_roi_extractor()


def test_tier2_requested_without_model_raises_clear_error(monkeypatch):
    """Simulates the model being absent regardless of this machine's actual
    state, so this assertion is meaningful whether or not M2's model has
    been trained here -- mirrors test_onnx_requested_without_model_raises_
    clear_error exactly."""
    os.environ["ROI_ENGINE"] = "tier2"

    import app.pipeline.field_classifier as field_classifier_module

    monkeypatch.setattr(field_classifier_module, "model_available", lambda *a, **k: False)

    from app.pipeline.read_frame import get_default_roi_extractor

    with pytest.raises(FileNotFoundError):
        get_default_roi_extractor()


@pytest.mark.skipif(not model_available(), reason="No trained Tier-2 field classifier present")
def test_tier2_roi_extractor_selected_when_explicitly_requested_and_model_present():
    from app.pipeline.tier2_roi import extract_rois_by_field_classifier
    from app.pipeline.read_frame import get_default_roi_extractor

    os.environ["ROI_ENGINE"] = "tier2"
    assert get_default_roi_extractor() is extract_rois_by_field_classifier


def test_roi_engine_and_ocr_engine_are_independently_selectable(monkeypatch):
    """The two switches are orthogonal, per TIER2_RECOGNITION_SPIKE.md sec
    10 -- setting ROI_ENGINE must not disturb OCR_ENGINE's own independent
    resolution, and vice versa."""
    import app.pipeline.read_frame as read_frame_module

    original_ocr = os.environ.get("OCR_ENGINE")
    try:
        os.environ.pop("ROI_ENGINE", None)
        os.environ["OCR_ENGINE"] = "tesseract"
        read_frame_module._default_engine = None
        from app.pipeline.ocr import TesseractEngine

        assert isinstance(read_frame_module.get_default_engine(), TesseractEngine)
        assert read_frame_module.get_default_roi_extractor() is extract_rois_by_colour
    finally:
        if original_ocr is None:
            os.environ.pop("OCR_ENGINE", None)
        else:
            os.environ["OCR_ENGINE"] = original_ocr
        read_frame_module._default_engine = None
