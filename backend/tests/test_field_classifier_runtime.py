"""M3 Phase 8, items 1-2: the Tier-2 FieldCNN runtime wrapper
(app.pipeline.field_classifier). Uses the real ONNX artifact and real
annotated crops from the frozen external-monitor dataset -- not mocks --
per the M3 task's "do not use only mocks" instruction. Skipped (not failed)
on a machine that hasn't trained the model, same convention
tests/test_engine_selection.py already uses for the digit CNN.
"""

import json
import os

import numpy as np
import pytest
from PIL import Image

from app.pipeline.field_classifier import (
    DEFAULT_ONNX_PATH,
    FieldClassifierEngine,
    FieldPrediction,
    model_available,
)

DATASET_DIR = os.path.join("app", "eval", "tier2_data", "external_monitor_video")

pytestmark = pytest.mark.skipif(not model_available(), reason="No trained Tier-2 field classifier present")


def _load_sample(sample_id: str):
    with open(os.path.join(DATASET_DIR, f"{sample_id}.json")) as f:
        label = json.load(f)
    img = np.array(Image.open(os.path.join(DATASET_DIR, f"{sample_id}.png")).convert("RGB"))
    return img, label


def _gt_crop(img: np.ndarray, box):
    x, y, w, h = box
    return img[y : y + h, x : x + w]


def test_model_loads_successfully():
    engine = FieldClassifierEngine()
    assert len(engine.labels) == 7
    assert "not_a_vital" in engine.labels
    assert engine.session is not None


def test_missing_model_raises_clear_filenotfounderror(tmp_path):
    with pytest.raises(FileNotFoundError):
        FieldClassifierEngine(onnx_path=str(tmp_path / "does_not_exist.onnx"))


def test_classify_empty_list_returns_empty():
    engine = FieldClassifierEngine()
    assert engine.classify([]) == []


def test_preprocessing_matches_m2_letterbox_convention():
    """The runtime's own _letterbox_gray must be bit-for-bit identical to
    the training-time implementation it was copied from
    (app.eval.tier2_field_dataset._letterbox_gray) -- otherwise the model
    sees different input in production than it was evaluated on."""
    from app.eval.tier2_field_dataset import _letterbox_gray as train_letterbox
    from app.pipeline.field_classifier import _letterbox_gray as runtime_letterbox

    rng = np.random.RandomState(0)
    for shape in [(40, 90, 3), (200, 30, 3), (64, 64), (1, 1, 3), (5, 400, 3)]:
        crop = rng.randint(0, 255, size=shape, dtype=np.uint8)
        expected = train_letterbox(crop, 64)
        actual = runtime_letterbox(crop, 64)
        assert np.array_equal(expected, actual), f"letterbox mismatch for shape {shape}"


def test_deterministic_inference_same_crop_same_result():
    engine = FieldClassifierEngine()
    crop = np.random.RandomState(1).randint(0, 255, size=(80, 140, 3), dtype=np.uint8)
    r1 = engine.classify([crop])[0]
    r2 = engine.classify([crop])[0]
    assert r1.label == r2.label
    assert r1.confidence == r2.confidence
    assert r1.probs == r2.probs


def test_known_hr_candidate_classified_as_hr():
    """sample_0017 is a held-out TEST image (never used in training,
    balancing, or checkpoint selection -- see tier2_field_dataset/report.json
    image_split.test) with a clean 'normal' condition, per M2 sec 14's own
    description of this frame as a clean pass across every vital."""
    img, label = _load_sample("sample_0017")
    crop = _gt_crop(img, label["rois"]["hr"])
    engine = FieldClassifierEngine()
    pred = engine.classify([crop])[0]
    assert isinstance(pred, FieldPrediction)
    assert pred.label == "hr"


def test_known_nibp_two_line_candidate_classified_as_nibp():
    img, label = _load_sample("sample_0017")
    crop = _gt_crop(img, label["rois"]["nibp"])
    engine = FieldClassifierEngine()
    pred = engine.classify([crop])[0]
    assert pred.label == "nibp"


def test_known_not_a_vital_candidate_rejected():
    """A large slice of the frame with no vital's ground-truth box in it at
    all -- a corner region -- should not be confidently classified as any
    real vital."""
    img, _label = _load_sample("sample_0017")
    h, w = img.shape[:2]
    # A slice of the header band, deliberately positioned away from every
    # ground-truth box in this sample.
    crop = img[0 : int(h * 0.08), 0 : int(w * 0.2)]
    engine = FieldClassifierEngine()
    pred = engine.classify([crop])[0]
    assert pred.label == "not_a_vital"


def test_probs_sum_to_one_and_confidence_matches_argmax():
    img, label = _load_sample("sample_0017")
    crop = _gt_crop(img, label["rois"]["temp"])
    engine = FieldClassifierEngine()
    pred = engine.classify([crop])[0]
    assert abs(sum(pred.probs) - 1.0) < 1e-4
    assert max(pred.probs) == pytest.approx(pred.confidence, abs=1e-6)


def test_batched_and_individual_inference_agree():
    """Runtime wrapper must not have any batch-order-dependent state (e.g.
    BatchNorm running in train mode would make this fail) -- classifying N
    crops together must give the same per-crop result as classifying them
    one at a time, since read_frame's per-candidate confidence has to be
    trustworthy regardless of how many other candidates were in the batch."""
    img, label = _load_sample("sample_0017")
    crops = [_gt_crop(img, label["rois"][v]) for v in ("hr", "spo2", "nibp", "etco2", "temp", "rr")]
    engine = FieldClassifierEngine()
    batched = engine.classify(crops)
    individual = [engine.classify([c])[0] for c in crops]
    for b, i in zip(batched, individual):
        assert b.label == i.label
        assert abs(b.confidence - i.confidence) < 1e-4
