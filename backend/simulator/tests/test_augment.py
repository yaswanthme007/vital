import random

import cv2
import numpy as np
import pytest
from PIL import Image

from simulator.randomize.augment import apply_perspective, augment_frame, augment_sample
from simulator.render.monitor_layout import LAYOUT_SIZES, render_monitor


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


def _rendered_frame(tmp_path, layout="grid"):
    out_path = tmp_path / f"{layout}.png"
    label = render_monitor(_sample_reading(), str(out_path), layout=layout)
    image = Image.open(out_path).convert("RGB")
    return image, label


@pytest.mark.parametrize("level", ["light", "heavy", "random"])
def test_augmented_image_differs_from_source(tmp_path, level):
    image, label = _rendered_frame(tmp_path)

    augmented_image, _rois, applied = augment_frame(image, label["rois"], level=level, seed=1)

    assert len(applied) >= 1
    assert augmented_image.size == image.size
    assert not np.array_equal(np.array(image), np.array(augmented_image))


def test_none_level_leaves_image_and_rois_unchanged(tmp_path):
    image, label = _rendered_frame(tmp_path)

    augmented_image, rois, applied = augment_frame(image, label["rois"], level="none", seed=1)

    assert applied == []
    assert np.array_equal(np.array(image), np.array(augmented_image))
    assert rois == label["rois"]


def test_augment_sample_preserves_values(tmp_path):
    image, label = _rendered_frame(tmp_path)

    _augmented_image, new_label = augment_sample(image, label, level="heavy", seed=2)

    assert new_label["values"] == label["values"]


@pytest.mark.parametrize("layout", sorted(LAYOUT_SIZES))
@pytest.mark.parametrize("level", ["light", "heavy", "random"])
def test_augmented_rois_stay_within_bounds(tmp_path, layout, level):
    image, label = _rendered_frame(tmp_path, layout=layout)
    width, height = LAYOUT_SIZES[layout]

    _augmented_image, rois, _applied = augment_frame(image, label["rois"], level=level, seed=3)

    assert set(rois.keys()) == set(label["rois"].keys())
    for vital, (x, y, w, h) in rois.items():
        assert x >= 0 and y >= 0, f"{layout}/{level}/{vital} ROI starts outside image bounds"
        assert x + w <= width, f"{layout}/{level}/{vital} ROI exceeds image width"
        assert y + h <= height, f"{layout}/{level}/{vital} ROI exceeds image height"
        assert w >= 0 and h >= 0, f"{layout}/{level}/{vital} ROI has negative size"


def test_pure_perspective_keeps_digit_centre_inside_warped_roi(tmp_path):
    image, label = _rendered_frame(tmp_path)
    img_array = np.array(image)
    rois = label["rois"]

    rng = random.Random(11)
    warped_array, warped_rois, meta = apply_perspective(img_array, rois, rng, severity=1.0)

    assert warped_array.shape == img_array.shape

    matrix = np.array(meta["matrix"], dtype=np.float32)

    for vital, roi in rois.items():
        x, y, w, h = roi
        center = np.array([[x + w / 2.0, y + h / 2.0]], dtype=np.float32)
        pts = center.reshape(-1, 1, 2)
        transformed_center = cv2.perspectiveTransform(pts, matrix).reshape(2)

        wx, wy, ww, wh = warped_rois[vital]
        assert wx <= transformed_center[0] <= wx + ww, f"{vital} warped centre x outside warped ROI"
        assert wy <= transformed_center[1] <= wy + wh, f"{vital} warped centre y outside warped ROI"


def test_unknown_augment_level_raises(tmp_path):
    image, label = _rendered_frame(tmp_path)
    with pytest.raises(ValueError):
        augment_frame(image, label["rois"], level="extreme", seed=1)


def test_augment_is_reproducible_with_seed(tmp_path):
    image, label = _rendered_frame(tmp_path)

    img_a, rois_a, applied_a = augment_frame(image, label["rois"], level="random", seed=77)
    img_b, rois_b, applied_b = augment_frame(image, label["rois"], level="random", seed=77)

    assert np.array_equal(np.array(img_a), np.array(img_b))
    assert rois_a == rois_b
    assert applied_a == applied_b
