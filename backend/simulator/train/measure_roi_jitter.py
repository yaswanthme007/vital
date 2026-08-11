"""Task 0 of S11: measures how much app.pipeline.roi's colour-extracted
boxes deviate from the simulator's ground-truth ROI boxes, across all 3
layouts and the full augmentation range. build_dataset.py reads the saved
stats here to parametrize "ROI jitter augmentation" on training crops.

Why this has to happen before any dataset gets built: at inference, the CNN
will only ever see crops produced by roi.py's colour extraction (S4 measured
IoU ~0.85-0.91 vs ground truth, i.e. slightly loose/shifted boxes) — never
the tight ground-truth box. Training only on tight crops would create a
train/serve skew the model has never seen at test time. This script
quantifies that skew directly (not just trusting the old S4 IoU figure) so
build_dataset.py's jitter augmentation is drawn from a real, current
measurement rather than a guessed range.

Usage:
    python -m simulator.train.measure_roi_jitter
    python -m simulator.train.measure_roi_jitter --count 400 --seed 0
"""

import argparse
import json
import os
import statistics
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.eval.harness import load_dataset  # noqa: E402
from app.pipeline.detect import detect_screen  # noqa: E402
from app.pipeline.roi import extract_rois_by_colour  # noqa: E402
from simulator.generate import generate_dataset  # noqa: E402

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

DEFAULT_STATS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roi_jitter_stats.json")
DEFAULT_SAMPLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "roi_jitter_sample"
)

Box = Tuple[float, float, float, float]


def _iou(a: Box, b: Box) -> float:
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


def _warp_box(box: Box, homography: np.ndarray) -> Box:
    """Ground truth is defined in the ORIGINAL image's coordinate space;
    when detect_screen rectifies the image, roi.py's boxes are measured on
    the rectified image instead -- so gt has to be warped through the same
    homography before the two are comparable."""
    x, y, w, h = box
    corners = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float32).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    x0, y0 = float(warped[:, 0].min()), float(warped[:, 1].min())
    x1, y1 = float(warped[:, 0].max()), float(warped[:, 1].max())
    return (x0, y0, x1 - x0, y1 - y0)


def _stats(values: List[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0, "p05": 0.0, "p95": 0.0}
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": ordered[0],
        "max": ordered[-1],
        "p05": ordered[int(0.05 * (len(ordered) - 1))],
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
    }


def measure(dataset_dir: str) -> dict:
    """dataset_dir: a simulator.generate --count output (sample_*.png/.json
    + manifest.json, per app.eval.harness.load_dataset's contract)."""
    samples = load_dataset(dataset_dir)

    ious: List[float] = []
    # Per-side deltas, normalized by the ground-truth box's own w/h so the
    # measurement is resolution/box-size independent. Positive = roi.py's
    # box is INSIDE (tighter than) ground truth on that side; negative =
    # roi.py's box extends PAST ground truth. These four are exactly what
    # build_dataset.py samples from to jitter a training crop.
    left_frac: List[float] = []
    right_frac: List[float] = []
    top_frac: List[float] = []
    bottom_frac: List[float] = []
    scale_w: List[float] = []
    scale_h: List[float] = []

    total_vitals = 0
    detected_count = 0
    miss_count = 0

    for sample in samples:
        img = np.array(Image.open(sample["png_path"]).convert("RGB"))
        gt_rois: Dict[str, list] = sample["label"].get("rois", {})

        screen = detect_screen(img)
        if screen.detected:
            detected_count += 1
        rois = extract_rois_by_colour(screen.image)

        for vital in VITALS:
            gt = gt_rois.get(vital)
            if not gt or gt[2] <= 0 or gt[3] <= 0:
                continue
            total_vitals += 1

            gt_box: Box = tuple(gt)
            if screen.detected and screen.homography is not None:
                gt_box = _warp_box(gt_box, screen.homography)

            result = rois.get(vital)
            if result is None:
                miss_count += 1
                continue

            gx, gy, gw, gh = gt_box
            px, py, pw, ph = result.box

            ious.append(_iou(gt_box, result.box))
            left_frac.append((px - gx) / gw)
            top_frac.append((py - gy) / gh)
            right_frac.append(((px + pw) - (gx + gw)) / gw)
            bottom_frac.append(((py + ph) - (gy + gh)) / gh)
            scale_w.append(pw / gw)
            scale_h.append(ph / gh)

    return {
        "sample_count": len(samples),
        "total_vitals_checked": total_vitals,
        "screen_detected_rate": (detected_count / len(samples)) if samples else None,
        "roi_miss_rate": (miss_count / total_vitals) if total_vitals else None,
        "iou": _stats(ious),
        "left_frac": _stats(left_frac),
        "right_frac": _stats(right_frac),
        "top_frac": _stats(top_frac),
        "bottom_frac": _stats(bottom_frac),
        "scale_w": _stats(scale_w),
        "scale_h": _stats(scale_h),
    }


def render_text_summary(stats: dict) -> str:
    lines = [
        f"Samples: {stats['sample_count']}   vitals checked: {stats['total_vitals_checked']}",
        f"detect_screen triggered (quad found): {stats['screen_detected_rate']:.1%}"
        if stats["screen_detected_rate"] is not None
        else "detect_screen triggered: n/a",
        f"roi.py miss rate (colour not found at all): {stats['roi_miss_rate']:.1%}"
        if stats["roi_miss_rate"] is not None
        else "roi.py miss rate: n/a",
        "",
        f"IoU vs ground truth: mean={stats['iou']['mean']:.3f}  "
        f"[p05={stats['iou']['p05']:.3f}, p95={stats['iou']['p95']:.3f}]  "
        f"min={stats['iou']['min']:.3f} max={stats['iou']['max']:.3f}  (n={stats['iou']['n']})",
        "",
        "Per-side offset, as a fraction of the ground-truth box's own w/h",
        "(this is exactly what build_dataset.py's jitter augmentation samples from):",
    ]
    for label, key in (("left", "left_frac"), ("right", "right_frac"), ("top", "top_frac"), ("bottom", "bottom_frac")):
        s = stats[key]
        lines.append(f"  {label:6s} mean={s['mean']:+.3f}  p05={s['p05']:+.3f}  p95={s['p95']:+.3f}  stdev={s['stdev']:.3f}")
    lines.append("")
    lines.append("Scale (predicted box size / ground-truth box size):")
    for label, key in (("width", "scale_w"), ("height", "scale_h")):
        s = stats[key]
        lines.append(f"  {label:6s} mean={s['mean']:.3f}  p05={s['p05']:.3f}  p95={s['p95']:.3f}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=300, help="Number of frames to measure over (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=0, help="Random seed (default: %(default)s)")
    parser.add_argument("--sample-dir", default=DEFAULT_SAMPLE_DIR, help="Where to render the measurement frames")
    parser.add_argument("--out", default=DEFAULT_STATS_PATH, help="Where to write the stats JSON")
    args = parser.parse_args()

    out_dir = os.path.dirname(args.sample_dir)
    dataset_id = os.path.basename(args.sample_dir)
    print(f"Rendering {args.count} measurement frames (layout=random, augment=random, seed={args.seed})...")
    generate_dataset(dataset_id, out_dir, args.count, layout="random", augment="random", seed=args.seed)

    print("Measuring roi.py vs ground truth...")
    stats = measure(args.sample_dir)

    summary = render_text_summary(stats)
    print()
    print(summary)

    with open(args.out, "w") as f:
        json.dump(stats, f, indent=2)
    print()
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
