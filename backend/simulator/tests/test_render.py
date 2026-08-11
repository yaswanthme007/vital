from PIL import Image

from simulator.render.monitor_layout import IMAGE_H, IMAGE_W, render_monitor


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


def test_render_monitor_creates_png_at_expected_size(tmp_path):
    out_path = tmp_path / "frame.png"

    render_monitor(_sample_reading(), str(out_path))

    assert out_path.exists()
    with Image.open(out_path) as img:
        assert img.size == (IMAGE_W, IMAGE_H)


def test_render_monitor_label_values_match_input(tmp_path):
    reading = _sample_reading()
    out_path = tmp_path / "frame.png"

    label = render_monitor(reading, str(out_path))

    assert label["values"] == reading


def test_render_monitor_rois_within_bounds(tmp_path):
    reading = _sample_reading()
    out_path = tmp_path / "frame.png"

    label = render_monitor(reading, str(out_path))

    assert set(label["rois"].keys()) == {"hr", "spo2", "nibp", "etco2", "temp", "rr"}
    for vital, (x, y, w, h) in label["rois"].items():
        assert x >= 0 and y >= 0, f"{vital} ROI starts outside image bounds"
        assert x + w <= IMAGE_W, f"{vital} ROI exceeds image width"
        assert y + h <= IMAGE_H, f"{vital} ROI exceeds image height"
        assert w > 0 and h > 0, f"{vital} ROI has non-positive size"
