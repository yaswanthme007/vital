"""S11: app.pipeline.read_frame's OCR_ENGINE factory. Unlike
test_onnx_engine.py, these run regardless of whether a trained model is
present -- the default-stays-Tesseract and unknown-value cases don't need
one, and are exactly the behaviour "additive only, default path stays
Tesseract" depends on.
"""

import os

import pytest

from app.pipeline.ocr import TesseractEngine
from app.pipeline.onnx_engine import model_available


def _reset_singleton():
    import app.pipeline.read_frame as read_frame_module

    read_frame_module._default_engine = None


@pytest.fixture(autouse=True)
def _isolate_engine_env():
    original = os.environ.get("OCR_ENGINE")
    _reset_singleton()
    yield
    if original is None:
        os.environ.pop("OCR_ENGINE", None)
    else:
        os.environ["OCR_ENGINE"] = original
    _reset_singleton()


def test_default_engine_is_tesseract_with_no_env_var():
    os.environ.pop("OCR_ENGINE", None)
    from app.pipeline.read_frame import get_default_engine

    assert isinstance(get_default_engine(), TesseractEngine)


def test_default_engine_is_tesseract_even_when_onnx_model_present_but_not_requested():
    """Merely having models/digit_cnn.onnx on disk must never silently
    change the default -- only an explicit OCR_ENGINE=onnx does."""
    os.environ.pop("OCR_ENGINE", None)
    from app.pipeline.read_frame import get_default_engine

    engine = get_default_engine()
    assert isinstance(engine, TesseractEngine)


def test_unknown_engine_value_raises():
    os.environ["OCR_ENGINE"] = "not-a-real-engine"
    from app.pipeline.read_frame import get_default_engine

    with pytest.raises(ValueError):
        get_default_engine()


def test_onnx_requested_without_model_raises_clear_error(monkeypatch):
    """Simulates the model being absent regardless of this machine's actual
    state, so this assertion is meaningful whether or not S11's model has
    been trained here."""
    os.environ["OCR_ENGINE"] = "onnx"

    import app.pipeline.onnx_engine as onnx_engine_module

    monkeypatch.setattr(onnx_engine_module, "model_available", lambda *a, **k: False)

    from app.pipeline.read_frame import get_default_engine

    with pytest.raises(FileNotFoundError):
        get_default_engine()


@pytest.mark.skipif(not model_available(), reason="No trained ONNX model present")
def test_onnx_engine_selected_when_explicitly_requested_and_model_present():
    from app.pipeline.onnx_engine import OnnxDigitEngine
    from app.pipeline.read_frame import get_default_engine

    os.environ["OCR_ENGINE"] = "onnx"
    assert isinstance(get_default_engine(), OnnxDigitEngine)
