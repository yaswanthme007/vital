"""M5.3 Phase 11: what does tracking actually cost per frame?

Measured in isolation, because the latency figures embedded in the accuracy
harness are taken while Tesseract subprocesses are competing for the same
cores and are therefore inflated and high-variance. Nothing here is claimed
as an improvement -- tracking is pure ADDED work on the calibrated path, and
the question is only whether the addition fits the frame budget.

Stages are timed separately so the answer identifies WHICH stage costs what:
    tracker init        once per WebSocket connection, never per frame
    track()             per frame -- ORB detect + match + RANSAC + gates
    ROI transform+crop  per frame -- the cheap part
    OCR                 per frame -- the part that already dominated in M5.2
Budgets are docs/ROADMAP.md's: <50ms/frame for the tracker, <=1.5s end to end.

Usage:
    python -m app.eval.m5_3_performance
"""

import json
import os
import statistics
import time
from typing import Callable, Dict, List

import cv2
import numpy as np
from PIL import Image

from app.models.calibration import CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import make_extractor, reference_pixel_boxes
from app.pipeline.layout_tracker import ORB_FEATURES, TRACK_MAX_DIM, LayoutTracker
from app.pipeline.ocr import TesseractEngine, _locate_tesseract_binary

import pytesseract

_resolved = _locate_tesseract_binary(None)
if _resolved:
    pytesseract.pytesseract.tesseract_cmd = _resolved

ENGINE = TesseractEngine()
OUT_DIR = "app/eval/tier2_data/m5_3_report"
DENSE_B = "app/eval/tier2_data/dense_B"
ANCHOR = "app/eval/tier2_data/dense_B_anchors/anchor_004971.json"

# docs/ROADMAP.md M5.3 acceptance and overall criterion 5.
TRACKER_BUDGET_MS = 50.0
END_TO_END_BUDGET_MS = 1500.0

WARMUP = 3


def _stats(values: List[float]) -> Dict[str, float]:
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "min": min(values),
        "max": max(values),
    }


def _profile_and_reference(target_w: int, target_h: int):
    with open(ANCHOR) as f:
        rois = json.load(f)["rois"]
    ref = np.array(Image.open(os.path.join(DENSE_B, "frame_000000.png")).convert("RGB"))
    src_h, src_w = ref.shape[:2]
    sx, sy = target_w / src_w, target_h / src_h
    ref = cv2.resize(ref, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    profile = CalibrationProfile(
        id="perf", reference_width=target_w, reference_height=target_h,
        roi_boxes={v: NormalizedBox(x=b[0] * sx / target_w, y=b[1] * sy / target_h,
                                    w=b[2] * sx / target_w, h=b[3] * sy / target_h)
                   for v, b in rois.items()},
        created_at=0, updated_at=0,
    )
    return profile, ref


def _frames(target_w: int, target_h: int, stride: int = 6) -> List[np.ndarray]:
    out = []
    for i in range(1, 270, stride):
        p = os.path.join(DENSE_B, f"frame_{i:06d}.png")
        if not os.path.exists(p):
            continue
        img = np.array(Image.open(p).convert("RGB"))
        out.append(cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_LINEAR))
    return out


def _time(fn: Callable, frames: List[np.ndarray]) -> List[float]:
    for f in frames[:WARMUP]:
        fn(f)
    out = []
    for f in frames:
        t0 = time.perf_counter()
        fn(f)
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def run_resolution(label: str, w: int, h: int) -> dict:
    profile, ref = _profile_and_reference(w, h)
    frames = _frames(w, h)
    exclude = list(reference_pixel_boxes(profile).values())

    inits = []
    for _ in range(5):
        t0 = time.perf_counter()
        LayoutTracker.from_reference_image(ref, exclude_boxes=exclude)
        inits.append((time.perf_counter() - t0) * 1000.0)
    tracker = LayoutTracker.from_reference_image(ref, exclude_boxes=exclude)

    track_ms = _time(tracker.track, frames)
    static_ms = _time(make_extractor(profile), frames)
    tracked_extract_ms = _time(make_extractor(profile, tracker=tracker), frames)

    # OCR over the tracked crops -- the stage that already dominated in M5.2.
    tracked_extractor = make_extractor(profile, tracker=tracker)
    ocr_ms = []
    for f in frames[:12]:
        rois = tracked_extractor(f)
        t0 = time.perf_counter()
        for vital, roi in rois.items():
            if roi is not None:
                ENGINE.read_vital(roi.crop, vital)
        ocr_ms.append((time.perf_counter() - t0) * 1000.0)

    track = _stats(track_ms)
    static = _stats(static_ms)
    tracked = _stats(tracked_extract_ms)
    ocr = _stats(ocr_ms)
    # The tracked ROI stage minus tracking itself == transform + crop.
    transform_only = max(0.0, tracked["mean"] - track["mean"])

    result = {
        "resolution": label, "width": w, "height": h,
        "track_max_dim": TRACK_MAX_DIM, "orb_features": ORB_FEATURES,
        "tracker_init_ms": _stats(inits),
        "track_ms": track,
        "static_roi_ms": static,
        "tracked_roi_ms": tracked,
        "transform_and_crop_ms_mean": transform_only,
        "ocr_all_fields_ms": ocr,
        "m5_2_frame_ms_mean": static["mean"] + ocr["mean"],
        "m5_3_frame_ms_mean": tracked["mean"] + ocr["mean"],
        "tracker_budget_ms": TRACKER_BUDGET_MS,
        "tracker_within_budget": track["mean"] <= TRACKER_BUDGET_MS,
        "end_to_end_budget_ms": END_TO_END_BUDGET_MS,
        "end_to_end_within_budget": (tracked["mean"] + ocr["mean"]) <= END_TO_END_BUDGET_MS,
    }

    print(f"\n--- {label} ({w}x{h}) ---")
    print(f"  tracker init (once per connection): {result['tracker_init_ms']['mean']:.0f} ms")
    print(f"  track() per frame                 : mean {track['mean']:.0f}  median {track['median']:.0f}  "
          f"p95 {track['p95']:.0f}  max {track['max']:.0f} ms   "
          f"[budget {TRACKER_BUDGET_MS:.0f} -> {'OK' if result['tracker_within_budget'] else 'MISSED'}]")
    print(f"  ROI transform + crop              : {transform_only:.1f} ms")
    print(f"  OCR, all fields                   : mean {ocr['mean']:.0f} ms")
    print(f"  frame total  M5.2 static          : {result['m5_2_frame_ms_mean']:.0f} ms")
    print(f"  frame total  M5.3 tracked         : {result['m5_3_frame_ms_mean']:.0f} ms   "
          f"[budget {END_TO_END_BUDGET_MS:.0f} -> {'OK' if result['end_to_end_within_budget'] else 'MISSED'}]")
    return result


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    results = [
        run_resolution("dense recording native", 640, 360),
        run_resolution("typical live camera", 1280, 720),
        run_resolution("high-res live camera", 1920, 1080),
    ]
    dest = os.path.join(OUT_DIR, "m5_3_performance.json")
    with open(dest, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {dest}")


if __name__ == "__main__":
    main()
