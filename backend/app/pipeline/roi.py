from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from app.pipeline.types import VitalRoiResult

# Mirrors the frontend colour conventions (also simulator/render/common.py's
# VITAL_COLORS) used to render each vital's numeral, so we can route pixels
# to a vital purely by colour.
VITAL_COLORS: Dict[str, Tuple[int, int, int]] = {
    "hr": (0, 255, 136),
    "spo2": (0, 212, 255),
    "nibp": (255, 71, 87),
    "etco2": (255, 214, 0),
    "temp": (255, 149, 0),
    "rr": (191, 90, 242),
}

# Two of our six target hues sit only 7 degrees apart (Temp ~18, EtCO2 ~25 on
# OpenCV's 0-179 scale), and the dark navy UI chrome (background/panel/border)
# shares SpO2's hue family at low value. A per-vital independent hue range
# can't cleanly separate these — instead every sufficiently bright/saturated
# pixel is assigned to whichever of the 6 target hues is CLOSEST (with
# circular wraparound, which also handles NIBP red sitting at the 0/179
# boundary for free), then capped by MAX_HUE_DIST so unrelated hues are
# rejected rather than force-assigned.
SAT_MIN = 100
VAL_MIN = 150
MAX_HUE_DIST = 20

# A single reading is often several disconnected glyphs (multi-digit numbers,
# NIBP's two stacked lines) that don't always merge into one connected
# component even after dilation — so every surviving component is UNIONed
# into the final box, not just the largest one. Two filters decide what
# "survives": a minimum pixel count (drops antialiasing/JPEG noise specks)
# and a minimum fill density = pixels / bbox area (drops thin, sparse shapes
# like the sidebar layout's decorative waveform traces, which share these
# same vital colours but are ~5x less dense than solid glyph strokes).
MIN_AREA_PIXELS = 15
MIN_AREA_FRAC = 0.00003
MIN_FILL_DENSITY = 0.12


def _rgb_to_hsv_scalar(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    pixel = np.uint8([[list(rgb)]])
    hsv = cv2.cvtColor(pixel, cv2.COLOR_RGB2HSV)[0, 0]
    return int(hsv[0]), int(hsv[1]), int(hsv[2])


def _nearest_hue_masks(
    hsv_img: np.ndarray,
    vital_colors: Dict[str, Tuple[int, int, int]],
    sat_min: int,
    val_min: int,
    max_hue_dist: int,
) -> Dict[str, np.ndarray]:
    vitals = list(vital_colors)
    target_hues = np.array([_rgb_to_hsv_scalar(vital_colors[v])[0] for v in vitals], dtype=np.int16)

    h_channel = hsv_img[..., 0].astype(np.int16)
    s_channel = hsv_img[..., 1]
    v_channel = hsv_img[..., 2]
    candidate = (s_channel >= sat_min) & (v_channel >= val_min)

    diffs = np.abs(h_channel[None, :, :] - target_hues[:, None, None])
    circular_dist = np.minimum(diffs, 180 - diffs)  # hue is circular mod 180 in OpenCV
    nearest_idx = np.argmin(circular_dist, axis=0)
    nearest_dist = np.min(circular_dist, axis=0)

    within_tolerance = nearest_dist <= max_hue_dist

    masks: Dict[str, np.ndarray] = {}
    for i, vital in enumerate(vitals):
        mask = candidate & within_tolerance & (nearest_idx == i)
        masks[vital] = (mask.astype(np.uint8)) * 255
    return masks


def extract_rois_by_colour(
    img: np.ndarray,
    sat_min: int = SAT_MIN,
    val_min: int = VAL_MIN,
    max_hue_dist: int = MAX_HUE_DIST,
    min_area_pixels: int = MIN_AREA_PIXELS,
    min_area_frac: float = MIN_AREA_FRAC,
    min_fill_density: float = MIN_FILL_DENSITY,
) -> Dict[str, Optional[VitalRoiResult]]:
    """Locate each vital's numeral by colour and return a tight crop + box.

    img: RGB uint8 ndarray (e.g. np.array(PIL.Image.open(path).convert("RGB"))).
    Returns {vital: VitalRoiResult | None} — None when that colour isn't
    present (or only present as noise) rather than raising.
    """
    h, w = img.shape[:2]
    total_area = h * w
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)

    masks = _nearest_hue_masks(hsv, VITAL_COLORS, sat_min, val_min, max_hue_dist)

    # Dilate to merge nearby glyph strokes/digits into connected groups before
    # labeling; the returned box is still measured from the RAW (non-dilated)
    # mask so it stays tight to the actual coloured pixels.
    kernel_size = max(3, int(min(h, w) * 0.015))
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    min_count = max(min_area_pixels, int(total_area * min_area_frac))

    results: Dict[str, Optional[VitalRoiResult]] = {}

    for vital, raw_mask in masks.items():
        if cv2.countNonZero(raw_mask) == 0:
            results[vital] = None
            continue

        dilated = cv2.dilate(raw_mask, kernel, iterations=1)
        num_labels, labels = cv2.connectedComponents(dilated)
        raw_bool = raw_mask > 0

        surviving: list = []
        for label in range(1, num_labels):
            group_mask = raw_bool & (labels == label)
            count = int(np.count_nonzero(group_mask))
            if count < min_count:
                continue
            ys, xs = np.where(group_mask)
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            density = count / (bw * bh)
            if density < min_fill_density:
                continue  # sparse/elongated — a waveform trace, not glyph strokes
            surviving.append((x0, y0, x1, y1))

        if not surviving:
            results[vital] = None
            continue

        x0 = min(b[0] for b in surviving)
        y0 = min(b[1] for b in surviving)
        x1 = max(b[2] for b in surviving)
        y1 = max(b[3] for b in surviving)
        box = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

        x, y, bw, bh = box
        crop = img[y : y + bh, x : x + bw].copy()
        results[vital] = VitalRoiResult(box=box, crop=crop, source_colour=VITAL_COLORS[vital])

    return results
