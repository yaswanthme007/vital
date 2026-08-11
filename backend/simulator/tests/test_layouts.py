import pytest
from PIL import Image

from simulator.render.monitor_layout import LAYOUT_SIZES, LAYOUTS, render_monitor


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


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_layout_round_trips_values_and_dimensions(tmp_path, layout):
    reading = _sample_reading()
    out_path = tmp_path / f"{layout}.png"

    label = render_monitor(reading, str(out_path), layout=layout)

    assert label["values"] == reading
    expected_size = LAYOUT_SIZES[layout]
    with Image.open(out_path) as img:
        assert img.size == expected_size


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_layout_rois_within_bounds(tmp_path, layout):
    reading = _sample_reading()
    out_path = tmp_path / f"{layout}.png"
    width, height = LAYOUT_SIZES[layout]

    label = render_monitor(reading, str(out_path), layout=layout)

    assert set(label["rois"].keys()) == {"hr", "spo2", "nibp", "etco2", "temp", "rr"}
    for vital, (x, y, w, h) in label["rois"].items():
        assert x >= 0 and y >= 0, f"{layout}/{vital} ROI starts outside image bounds"
        assert x + w <= width, f"{layout}/{vital} ROI exceeds image width"
        assert y + h <= height, f"{layout}/{vital} ROI exceeds image height"
        assert w > 0 and h > 0, f"{layout}/{vital} ROI has non-positive size"


def test_layouts_are_spatially_distinct():
    # Different canvas sizes confirm these aren't just recolours of one grid.
    sizes = list(LAYOUT_SIZES.values())
    assert len(set(sizes)) == len(sizes)


def test_unknown_layout_raises(tmp_path):
    with pytest.raises(ValueError):
        render_monitor(_sample_reading(), str(tmp_path / "x.png"), layout="nonexistent")


def test_default_layout_is_grid_for_backward_compatibility(tmp_path):
    from simulator.render.monitor_layout import IMAGE_H, IMAGE_W

    out_path = tmp_path / "default.png"
    render_monitor(_sample_reading(), str(out_path))

    with Image.open(out_path) as img:
        assert img.size == (IMAGE_W, IMAGE_H) == LAYOUT_SIZES["grid"]
