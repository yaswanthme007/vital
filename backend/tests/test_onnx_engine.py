"""S11: OnnxDigitEngine tests. Skipped entirely (not failed) if the trained
model isn't present -- e.g. a fresh clone that hasn't run
simulator/train/train_cnn.py yet -- so this file never threatens "keep ALL
prior tests green" regardless of whether the model has been (re)generated.
"""

import numpy as np
import pytest
from PIL import Image

from app.pipeline.onnx_engine import DEFAULT_ONNX_PATH, model_available

pytestmark = pytest.mark.skipif(not model_available(), reason=f"No trained ONNX model at {DEFAULT_ONNX_PATH}")


def _generate_clean_sample(tmp_path, sample_id: str, layout: str, seed: int) -> dict:
    from simulator.generate import generate_sample

    result = generate_sample(sample_id, str(tmp_path), layout=layout, augment="none", seed=seed)
    return result


def test_onnx_engine_loads():
    from app.pipeline.onnx_engine import OnnxDigitEngine

    engine = OnnxDigitEngine()
    assert engine.labels[-1] == "blank"
    assert len(engine.labels) == 13


def test_onnx_engine_reads_clean_ground_truth_crops(tmp_path):
    """Isolates CNN correctness from roi.py's own extraction accuracy by
    cropping with the exact ground-truth ROI box (no jitter) on a clean,
    unaugmented frame -- the easiest possible case, so it should read
    almost every vital exactly right."""
    from app.pipeline.onnx_engine import OnnxDigitEngine

    engine = OnnxDigitEngine()
    correct = 0
    total = 0

    for i, layout in enumerate(("grid", "sidebar", "compact")):
        result = _generate_clean_sample(tmp_path, f"clean_{i}", layout, seed=100 + i)
        record = result["record"]
        img = np.array(Image.open(result["png_path"]).convert("RGB"))
        values = record["values"]

        for vital, roi in record["rois"].items():
            x, y, w, h = roi
            if w <= 0 or h <= 0:
                continue
            crop = img[y : y + h, x : x + w]
            value, confidence = engine.read_vital(crop, vital)
            total += 1

            if vital == "nibp":
                if (
                    value is not None
                    and value.systolic == round(values["nibpSystolic"])
                    and value.diastolic == round(values["nibpDiastolic"])
                    and value.mean == round(values["nibpMean"])
                ):
                    correct += 1
            elif vital == "temp":
                if value is not None and abs(value - values["temp"]) < 1e-6:
                    correct += 1
            else:
                if value is not None and value == round(values[vital]):
                    correct += 1

    # Not 100% -- NIBP's smaller mean line is the known weak point (see
    # measure_roi_jitter's segmentation match-rate report) -- but the vast
    # majority of clean, tightly-cropped reads should be exact.
    assert total > 0
    assert correct / total >= 0.7, f"only {correct}/{total} clean ground-truth crops read correctly"


def test_onnx_engine_never_raises_on_empty_crop():
    from app.pipeline.onnx_engine import OnnxDigitEngine

    engine = OnnxDigitEngine()
    empty = np.zeros((0, 0, 3), dtype=np.uint8)
    value, confidence = engine.read_vital(empty, "hr")
    assert value is None
    assert confidence == 0.0

    value, confidence = engine.read_vital(empty, "nibp")
    assert value.systolic is None and value.diastolic is None and value.mean is None
    assert confidence == 0.0


def test_read_frame_works_with_onnx_engine_selected(tmp_path):
    from app.pipeline.onnx_engine import OnnxDigitEngine
    from app.pipeline.read_frame import VITALS, read_frame

    result = _generate_clean_sample(tmp_path, "full_pipeline", "grid", seed=200)
    img = np.array(Image.open(result["png_path"]).convert("RGB"))

    reading, confidences = read_frame(img, engine=OnnxDigitEngine())

    expected_keys = {"hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr"}
    assert set(reading.keys()) == expected_keys
    assert set(confidences.keys()) == set(VITALS)
    for v in confidences.values():
        assert 0.0 <= v <= 100.0
