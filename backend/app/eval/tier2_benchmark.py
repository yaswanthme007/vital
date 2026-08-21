"""Tier-2 Milestone 1 benchmark: colour-agnostic candidate generation vs the
Tier-1 colour ROI baseline, over a dataset in sample_XXXX.png/.json shape
(real-monitor or synthetic/proxy).

ISOLATED FROM PRODUCTION: this script only ever calls
app.pipeline.detect.detect_screen() (read-only, colour-agnostic, unchanged)
and app.pipeline.roi.extract_rois_by_colour() (read-only baseline
measurement) -- it never modifies them, never touches read_frame()'s call
graph, and OCR/reconcile/persistence/alerts/WebSocket/frontend are not
imported at all.

Usage:
    python -m app.eval.tier2_benchmark --dataset <path> [--out <dir>]
    python -m app.eval.tier2_benchmark --dataset <path> --iou-threshold 0.3

See TIER2_M1_BENCHMARK_REPORT.md for the IoU-threshold rationale (0.3,
matching TIER2_RECOGNITION_SPIKE.md section 03/08's own established
convention for candidate-generation recall -- a deliberately looser bar than
test_pipeline_roi.py's 0.6, which scores tight FINAL colour-ROI boxes, not
coarse pre-classification candidates).
"""

import argparse
import json
import os
import time
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageDraw

from app.eval.tier2_candidates import GENERATORS, GENERATORS_V2
from app.eval.tier2_common import VITALS, Box, best_iou, fmt_ms, fmt_pct, iou, stats, warp_box
from app.eval.harness import load_dataset
from app.pipeline.detect import _find_screen_quad, detect_screen
from app.pipeline.roi import extract_rois_by_colour

DEFAULT_IOU_THRESHOLD = 0.3
FP_IOU_CEILING = 0.1  # a candidate below this IoU with every GT box counts as a false positive
DEBUG_COLOR_GT = (57, 255, 20)       # lime green
DEBUG_COLOR_ADAPTIVE = (0, 200, 255)  # cyan
DEBUG_COLOR_CANNY = (255, 60, 200)    # magenta

CONDITION_LABELS = {
    "perspective": "perspective",
    "glare": "glare",
    "dim": "low_brightness",
    "blur": "blur",
    "noise": "noise",
    "occlusion": "occlusion",
}


def _condition_buckets(augmentations: List[dict]) -> List[str]:
    if not augmentations:
        return ["normal"]
    return sorted({CONDITION_LABELS.get(e["type"], e["type"]) for e in augmentations})


def _method_recall_for_sample(candidate_boxes: List[Box], gt_box: Box, threshold: float) -> Dict:
    bi = best_iou(candidate_boxes, gt_box)
    return {"found": bi >= threshold, "best_iou": bi, "candidate_count": len(candidate_boxes)}


def _tier1_recall_for_sample(roi_result, gt_box: Box, threshold: float) -> Dict:
    if roi_result is None:
        return {"found": False, "best_iou": 0.0}
    bi = iou(roi_result.box, gt_box)
    return {"found": bi >= threshold, "best_iou": bi}


def evaluate_sample(
    png_path: str, label: dict, iou_threshold: float, timings: Dict[str, List[float]], generators: Dict = GENERATORS
) -> Optional[dict]:
    img = np.array(Image.open(png_path).convert("RGB"))
    gt_rois: Dict[str, list] = label.get("rois", {})
    if not gt_rois:
        return None

    t0 = time.perf_counter()
    _ = _find_screen_quad(img, 0.5)
    t1 = time.perf_counter()
    screen = detect_screen(img)
    t2 = time.perf_counter()
    timings["screen_quad_find"].append(t1 - t0)
    timings["screen_detect_total"].append(t2 - t0)
    # Perspective-correction-only latency is measured separately in
    # _rectify_only_timing() (a repeated-iteration pass over a subset) --
    # subtracting these two single noisy wall-clock reads here would be a
    # much worse estimate than replaying just the warp step directly.

    work_img = screen.image

    t0 = time.perf_counter()
    tier1_rois = extract_rois_by_colour(work_img)
    timings["tier1_colour_roi"].append(time.perf_counter() - t0)

    candidates_by_method: Dict[str, List[Box]] = {}
    for name, generator in generators.items():
        t0 = time.perf_counter()
        boxes = generator(work_img)
        timings[name].append(time.perf_counter() - t0)
        candidates_by_method[name] = boxes

    fields = {}
    for vital in VITALS:
        gt_raw = gt_rois.get(vital)
        if not gt_raw or gt_raw[2] <= 0 or gt_raw[3] <= 0:
            continue
        gt_box: Box = tuple(gt_raw)
        if screen.detected and screen.homography is not None:
            gt_box = warp_box(gt_box, screen.homography)

        entry = {
            "gt_box": gt_box,
            "tier1": _tier1_recall_for_sample(tier1_rois.get(vital), gt_box, iou_threshold),
        }
        for name, boxes in candidates_by_method.items():
            entry[name] = _method_recall_for_sample(boxes, gt_box, iou_threshold)
        fields[vital] = entry

    # False positives: candidates that don't correspond (IoU < FP_IOU_CEILING)
    # to ANY ground-truth vital box in this sample.
    gt_boxes_warped = [fields[v]["gt_box"] for v in fields]
    false_positives = {}
    for name, boxes in candidates_by_method.items():
        fp = sum(1 for b in boxes if not gt_boxes_warped or max((iou(b, g) for g in gt_boxes_warped), default=0.0) < FP_IOU_CEILING)
        false_positives[name] = fp

    return {
        "id": label.get("id"),
        "layout": label.get("layout"),
        "conditions": _condition_buckets(label.get("augmentations", [])),
        "screen_detected": screen.detected,
        "fields": fields,
        "false_positives": false_positives,
        "candidate_counts": {name: len(boxes) for name, boxes in candidates_by_method.items()},
    }


def _rectify_only_timing(png_path: str, n: int = 10) -> List[float]:
    """detect_screen() bundles quad-find + rectify in one call. To separate
    "perspective correction" latency specifically, this replays JUST the
    warpPerspective step using an already-found quad (so quad-finding cost
    isn't counted twice) -- a more honest decomposition than subtracting two
    noisy wall-clock measurements."""
    import cv2

    from app.pipeline.detect import _order_quad_points

    img = np.array(Image.open(png_path).convert("RGB"))
    quad = _find_screen_quad(img, 0.5)
    if quad is None:
        return []
    ordered = _order_quad_points(quad)
    tl, tr, br, bl = ordered
    dst_w = max(int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))), 10)
    dst_h = max(int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))), 10)
    dst = np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)

    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        matrix = cv2.getPerspectiveTransform(ordered, dst)
        cv2.warpPerspective(img, matrix, (dst_w, dst_h))
        times.append(time.perf_counter() - t0)
    return times


def aggregate(results: List[dict], iou_threshold: float, generators: Dict = GENERATORS) -> dict:
    methods = ["tier1"] + list(generators.keys())
    per_vital: Dict[str, Dict[str, dict]] = {v: {m: {"n": 0, "found": 0} for m in methods} for v in VITALS}
    overall = {m: {"n": 0, "found": 0} for m in methods}

    for r in results:
        for vital, entry in r["fields"].items():
            for m in methods:
                per_vital[vital][m]["n"] += 1
                overall[m]["n"] += 1
                if entry[m]["found"]:
                    per_vital[vital][m]["found"] += 1
                    overall[m]["found"] += 1

    def _recall(d):
        return (d["found"] / d["n"]) if d["n"] else None

    per_vital_recall = {v: {m: _recall(per_vital[v][m]) for m in methods} for v in VITALS}
    overall_recall = {m: _recall(overall[m]) for m in methods}

    avg_candidates = {}
    avg_fp = {}
    for name in generators:
        counts = [r["candidate_counts"][name] for r in results]
        fps = [r["false_positives"][name] for r in results]
        avg_candidates[name] = (sum(counts) / len(counts)) if counts else None
        avg_fp[name] = (sum(fps) / len(fps)) if fps else None

    # Robustness split by condition bucket.
    by_condition: Dict[str, Dict[str, dict]] = {}
    all_conditions = sorted({c for r in results for c in r["conditions"]})
    for cond in all_conditions:
        subset = [r for r in results if cond in r["conditions"]]
        cond_overall = {m: {"n": 0, "found": 0} for m in methods}
        for r in subset:
            for entry in r["fields"].values():
                for m in methods:
                    cond_overall[m]["n"] += 1
                    if entry[m]["found"]:
                        cond_overall[m]["found"] += 1
        by_condition[cond] = {
            "sample_count": len(subset),
            "recall": {m: _recall(cond_overall[m]) for m in methods},
        }

    screen_detect_rate = (sum(1 for r in results if r["screen_detected"]) / len(results)) if results else None

    return {
        "sample_count": len(results),
        "iou_threshold": iou_threshold,
        "screen_detect_rate": screen_detect_rate,
        "per_vital_recall": per_vital_recall,
        "overall_recall": overall_recall,
        "avg_candidate_count": avg_candidates,
        "avg_false_positives_per_image": avg_fp,
        "by_condition": by_condition,
    }


def render_debug_image(png_path: str, result: dict, out_path: str, generators: Dict = GENERATORS) -> None:
    img = Image.open(png_path).convert("RGB")
    screen = detect_screen(np.array(img))
    canvas = Image.fromarray(screen.image).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    for vital, entry in result["fields"].items():
        x, y, w, h = entry["gt_box"]
        draw.rectangle([x, y, x + w, y + h], outline=DEBUG_COLOR_GT, width=3)
        draw.text((x, max(0, y - 14)), f"gt:{vital}", fill=DEBUG_COLOR_GT)

    def _draw_boxes(boxes, color, dash_offset):
        for (x, y, w, h) in boxes:
            x0, y0 = x + dash_offset, y + dash_offset
            x1, y1 = max(x0 + 1, x + w - dash_offset), max(y0 + 1, y + h - dash_offset)
            draw.rectangle([x0, y0, x1, y1], outline=color, width=2)

    # Candidate boxes aren't stored on `result` (only recall summary is) --
    # regenerate them here for drawing. Cheap relative to file I/O.
    _draw_boxes(generators["adaptive_threshold"](screen.image), DEBUG_COLOR_ADAPTIVE, 0)
    _draw_boxes(generators["canny_contour"](screen.image), DEBUG_COLOR_CANNY, 3)

    legend_y = 6
    draw.text((6, legend_y), "green=ground truth  cyan=adaptive-threshold  magenta=canny", fill=(255, 255, 0))

    canvas.save(out_path)


def render_text_summary(summary: dict, generators: Dict = GENERATORS) -> str:
    methods = ["tier1"] + list(generators.keys())
    names = {"tier1": "Tier-1 colour", "adaptive_threshold": "Adaptive threshold", "canny_contour": "Canny+contour"}
    lines = []
    lines.append(f"Samples: {summary['sample_count']}   IoU threshold: {summary['iou_threshold']}")
    lines.append(f"Screen detected (quad found): {fmt_pct(summary['screen_detect_rate'])}")
    lines.append("")
    lines.append("Per-vital recall:")
    header = "  " + "vital".ljust(10) + "".join(names[m].rjust(20) for m in methods)
    lines.append(header)
    for v in VITALS:
        row = "  " + v.ljust(10) + "".join(fmt_pct(summary["per_vital_recall"][v][m]).rjust(20) for m in methods)
        lines.append(row)
    lines.append("")
    lines.append("Overall recall:")
    lines.append("  " + "".join(f"{names[m]}: {fmt_pct(summary['overall_recall'][m])}   " for m in methods))
    lines.append("")
    lines.append("Avg candidates/image, avg false positives/image (candidate generators only):")
    for name in generators:
        lines.append(f"  {names[name]:22s} candidates={summary['avg_candidate_count'][name]:.1f}  false_positives={summary['avg_false_positives_per_image'][name]:.2f}")
    lines.append("")
    if summary["by_condition"]:
        lines.append("Recall by condition:")
        for cond, data in summary["by_condition"].items():
            row = "  " + cond.ljust(16) + f"n={data['sample_count']:4d}  " + "  ".join(f"{names[m]}={fmt_pct(data['recall'][m])}" for m in methods)
            lines.append(row)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True, help="Dataset dir: sample_*.png/.json pairs")
    parser.add_argument("--out", default=None, help="Output dir for report + debug images (default: <dataset>/tier2_m1_report)")
    parser.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD)
    parser.add_argument("--debug-n", type=int, default=6, help="Number of debug overlay images to render")
    parser.add_argument("--latency-iters", type=int, default=20, help="Repeats per image for latency measurement")
    parser.add_argument("--label", default=None, help="Free-text label for this run, e.g. 'REAL MONITOR' or 'SYNTHETIC PROXY' -- stamped into the report")
    parser.add_argument(
        "--generators",
        default="v1",
        choices=["v1", "v2"],
        help="Which candidate-generator implementations to benchmark. v1 (default) is the original, "
        "unmodified M1 code -- reruns reproduce the original M1 numbers exactly. v2 is the M1.1-hardened "
        "version (line-artifact stripping, wider merge kernel, vertical-stack merge, aspect filter). "
        "v2's report is written to a separate <out>_v2 dir so v1's saved report is never overwritten.",
    )
    args = parser.parse_args()

    generators = GENERATORS if args.generators == "v1" else GENERATORS_V2

    samples = load_dataset(args.dataset)
    if not samples:
        raise SystemExit(f"No sample_*.json/.png pairs found under {args.dataset}")

    default_out = os.path.join(args.dataset, "tier2_m1_report" if args.generators == "v1" else "tier2_m1_report_v2")
    out_dir = args.out or default_out
    debug_dir = os.path.join(out_dir, "debug")
    os.makedirs(debug_dir, exist_ok=True)

    timings: Dict[str, List[float]] = {
        "screen_quad_find": [],
        "screen_detect_total": [],
        "tier1_colour_roi": [],
        **{name: [] for name in generators},
    }

    results = []
    for s in samples:
        r = evaluate_sample(s["png_path"], s["label"], args.iou_threshold, timings, generators=generators)
        if r is not None:
            results.append(r)

    # Dedicated repeated-iteration latency pass over a subset, so latency
    # isn't reported from a single noisy measurement (Phase 9 requirement).
    latency_subset = samples[: min(5, len(samples))]
    rectify_only_times: List[float] = []
    for s in latency_subset:
        rectify_only_times.extend(_rectify_only_timing(s["png_path"], n=args.latency_iters))
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        for _ in range(args.latency_iters):
            t0 = time.perf_counter()
            _find_screen_quad(img, 0.5)
            timings["screen_quad_find"].append(time.perf_counter() - t0)
        screen = detect_screen(img)
        for _ in range(args.latency_iters):
            t0 = time.perf_counter()
            extract_rois_by_colour(screen.image)
            timings["tier1_colour_roi"].append(time.perf_counter() - t0)
        for name, generator in generators.items():
            for _ in range(args.latency_iters):
                t0 = time.perf_counter()
                generator(screen.image)
                timings[name].append(time.perf_counter() - t0)

    latency_report = {
        "screen_quad_find": stats(timings["screen_quad_find"]),
        "perspective_correct_rectify_only": stats(rectify_only_times),
        "tier1_colour_roi": stats(timings["tier1_colour_roi"]),
        **{name: stats(timings[name]) for name in generators},
    }

    summary = aggregate(results, args.iou_threshold, generators=generators)
    summary["dataset_dir"] = args.dataset
    summary["run_label"] = args.label
    summary["generators_version"] = args.generators

    debug_images = []
    for r, s in zip(results[: args.debug_n], samples[: args.debug_n]):
        debug_path = os.path.join(debug_dir, f"debug_{r['id']}.png")
        render_debug_image(s["png_path"], r, debug_path, generators=generators)
        debug_images.append(debug_path)

    report = {"summary": summary, "latency_seconds": latency_report, "results": results, "debug_images": debug_images}

    out_json = os.path.join(out_dir, "tier2_m1_report.json")
    with open(out_json, "w") as f:
        json.dump(report, f, indent=2, default=lambda o: list(o) if isinstance(o, tuple) else o)

    summary_text = render_text_summary(summary, generators=generators)
    latency_text = "\n".join(
        f"  {name:32s} mean={fmt_ms(v['mean'])}  median={fmt_ms(v['median'])}  p95={fmt_ms(v['p95'])}  (n={v['n']})"
        for name, v in latency_report.items()
    )
    full_text = f"{args.label or ''}\n{summary_text}\n\nLatency:\n{latency_text}\n"
    out_txt = os.path.join(out_dir, "tier2_m1_report.txt")
    with open(out_txt, "w") as f:
        f.write(full_text)

    print(full_text)
    print(f"Wrote {out_json}")
    print(f"Wrote {out_txt}")
    print(f"Wrote {len(debug_images)} debug images to {debug_dir}")


if __name__ == "__main__":
    main()
