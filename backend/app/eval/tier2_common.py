"""Shared helpers for the Tier-2 M1 benchmark (app.eval.tier2_benchmark,
tier2_candidates, tier2_proxy). Isolated from the production pipeline --
nothing here is imported by app.pipeline.* or app.sources.*.

Box/IoU/homography-warp math intentionally mirrors
simulator/train/measure_roi_jitter.py's (already-proven) implementation
rather than reinventing it, per TIER2_RECOGNITION_SPIKE.md section 08's
instruction to reuse the existing IoU/eval-harness math unmodified.
"""

import statistics
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# (x, y, w, h) -- matches app.pipeline.types.Box exactly.
Box = Tuple[float, float, float, float]

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")


def iou(a: Box, b: Box) -> float:
    ax0, ay0, aw, ah = a
    ax1, ay1 = ax0 + aw, ay0 + ah
    bx0, by0, bw, bh = b
    bx1, by1 = bx0 + bw, by0 + bh

    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def warp_box(box: Box, homography: np.ndarray) -> Box:
    """Ground truth is annotated in the ORIGINAL image's coordinate space;
    detect_screen() rectifies the image before candidate generation runs, so
    gt has to be warped through the same homography before it's comparable
    to a candidate box. Identical to measure_roi_jitter.py's _warp_box."""
    x, y, w, h = box
    corners = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float32).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    x0, y0 = float(warped[:, 0].min()), float(warped[:, 1].min())
    x1, y1 = float(warped[:, 0].max()), float(warped[:, 1].max())
    return (x0, y0, x1 - x0, y1 - y0)


def best_iou(candidate_boxes: List[Box], gt_box: Box) -> float:
    if not candidate_boxes:
        return 0.0
    return max(iou(c, gt_box) for c in candidate_boxes)


def stats(values: List[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "min": ordered[0],
        "max": ordered[-1],
    }


def fmt_ms(seconds: Optional[float]) -> str:
    return f"{seconds * 1000:.1f}ms" if seconds is not None else "n/a"


def fmt_pct(value: Optional[float]) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"
