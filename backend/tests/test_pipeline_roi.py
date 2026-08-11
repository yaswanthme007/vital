import random

import numpy as np
import pytest
from PIL import Image

from app.pipeline.detect import detect_screen
from app.pipeline.roi import VITAL_COLORS, extract_rois_by_colour
from app.pipeline.types import ScreenDetectionResult, VitalRoiResult
from simulator.randomize.augment import apply_perspective
from simulator.render.monitor_layout import LAYOUTS, render_monitor

# "Reasonable" per the task — our measured IoUs on clean frames sit at
# 0.85-0.98 across all 3 layouts/all 6 vitals, so 0.6 leaves a healthy margin
# while still catching a real regression.
IOU_THRESHOLD = 0.6


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


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


def _rendered(tmp_path, layout):
    out_path = tmp_path / f"{layout}.png"
    label = render_monitor(_sample_reading(), str(out_path), layout=layout)
    image = np.array(Image.open(out_path).convert("RGB"))
    return image, label


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_colour_rois_overlap_ground_truth_on_clean_frame(tmp_path, layout):
    image, label = _rendered(tmp_path, layout)

    results = extract_rois_by_colour(image)

    for vital, gt_box in label["rois"].items():
        result = results[vital]
        assert result is not None, f"{layout}/{vital}: colour ROI not found on a clean frame"
        assert isinstance(result, VitalRoiResult)
        iou = _iou(gt_box, result.box)
        assert iou >= IOU_THRESHOLD, f"{layout}/{vital}: IoU {iou:.3f} below threshold ({result.box} vs gt {gt_box})"
        # The crop must actually correspond to the returned box.
        x, y, w, h = result.box
        assert result.crop.shape[:2] == (h, w)


def test_colour_roi_crops_are_nonempty_and_match_box(tmp_path):
    image, _label = _rendered(tmp_path, "grid")
    results = extract_rois_by_colour(image)
    for vital, result in results.items():
        assert result is not None
        assert result.crop.size > 0
        assert result.source_colour == VITAL_COLORS[vital]


def test_detect_screen_on_perspective_warped_frame_does_not_crash(tmp_path):
    image, label = _rendered(tmp_path, "grid")
    rng = random.Random(5)
    warped_image, _warped_rois, _meta = apply_perspective(image, label["rois"], rng, severity=0.4)

    result = detect_screen(warped_image)

    assert isinstance(result, ScreenDetectionResult)
    assert isinstance(result.image, np.ndarray)
    assert result.image.size > 0
    if result.detected:
        assert result.homography is not None
        assert result.corners is not None
    else:
        # Graceful fallback: unchanged image, no exception.
        assert result.homography is None
        assert np.array_equal(result.image, warped_image)


def test_colour_rois_still_found_after_perspective_warp(tmp_path):
    image, label = _rendered(tmp_path, "grid")
    rng = random.Random(5)
    warped_image, _warped_rois, _meta = apply_perspective(image, label["rois"], rng, severity=0.4)

    screen = detect_screen(warped_image)
    results = extract_rois_by_colour(screen.image)

    found = [vital for vital, r in results.items() if r is not None]
    assert len(found) == 6, f"expected all 6 vitals still detectable after a mild warp, got {found}"


def test_missing_colour_returns_none_not_exception():
    # A tiny synthetic frame containing only HR-green — every other vital's
    # colour is genuinely absent.
    img = np.zeros((60, 60, 3), dtype=np.uint8)
    img[10:30, 10:40] = VITAL_COLORS["hr"]

    results = extract_rois_by_colour(img)

    assert results["hr"] is not None
    for vital in VITAL_COLORS:
        if vital == "hr":
            continue
        assert results[vital] is None


def test_blank_image_returns_all_none():
    img = np.zeros((40, 40, 3), dtype=np.uint8)
    results = extract_rois_by_colour(img)
    assert all(result is None for result in results.values())
