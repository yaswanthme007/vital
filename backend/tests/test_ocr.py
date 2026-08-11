import numpy as np
import pytest
from PIL import Image

from app.pipeline.ocr import NibpValue, TesseractEngine
from app.pipeline.roi import extract_rois_by_colour
from simulator.render.monitor_layout import LAYOUTS, render_monitor


@pytest.fixture(scope="module")
def engine():
    return TesseractEngine()


def _sample_reading():
    return {
        "hr": 72,
        "spo2": 98,
        "nibpSystolic": 120,
        "nibpDiastolic": 78,
        "nibpMean": 92,
        "etco2": 38,
        "temp": 36.8,
        "rr": 14,
        "timestamp": 1700000000000,
    }


def _clean_rois(tmp_path, layout):
    out_path = tmp_path / f"{layout}.png"
    render_monitor(_sample_reading(), str(out_path), layout=layout)
    img = np.array(Image.open(out_path).convert("RGB"))
    return extract_rois_by_colour(img)


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_common_vitals_read_correctly_on_clean_frame(tmp_path, layout, engine):
    rois = _clean_rois(tmp_path, layout)
    expected = {"hr": 72, "spo2": 98, "etco2": 38, "rr": 14, "temp": 36.8}

    for vital, expected_value in expected.items():
        crop = rois[vital].crop
        value, confidence = engine.read_vital(crop, vital)
        assert value is not None, f"{layout}/{vital}: expected {expected_value}, got None"
        assert value == pytest.approx(expected_value, abs=0.05), f"{layout}/{vital}: expected {expected_value}, got {value}"
        assert confidence > 0


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_nibp_reads_with_tolerance(tmp_path, layout, engine):
    rois = _clean_rois(tmp_path, layout)

    value, confidence = engine.read_vital(rois["nibp"].crop, "nibp")

    assert isinstance(value, NibpValue)
    assert value.systolic is not None and abs(value.systolic - 120) <= 2
    assert value.diastolic is not None and abs(value.diastolic - 78) <= 2
    # Mean is a much smaller font on a shared crop — allow it to be missed
    # without failing the test, but it must be close if it WAS read.
    if value.mean is not None:
        assert abs(value.mean - 92) <= 3
    assert confidence > 0


def test_unreadable_input_returns_none_not_exception(engine):
    blank = np.zeros((30, 60, 3), dtype=np.uint8)
    value, confidence = engine.read_vital(blank, "hr")
    assert value is None
    assert confidence == 0.0

    empty = np.zeros((0, 0, 3), dtype=np.uint8)
    value, confidence = engine.read_vital(empty, "hr")
    assert value is None
    assert confidence == 0.0

    tiny = np.zeros((1, 1, 3), dtype=np.uint8)
    value, confidence = engine.read_vital(tiny, "rr")
    assert value is None
    assert confidence == 0.0


def test_unreadable_nibp_input_returns_all_none_not_exception(engine):
    blank = np.zeros((40, 80, 3), dtype=np.uint8)
    value, confidence = engine.read_vital(blank, "nibp")
    assert isinstance(value, NibpValue)
    assert value.systolic is None
    assert value.diastolic is None
    assert value.mean is None
    assert confidence == 0.0
