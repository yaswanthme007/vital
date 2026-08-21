"""M2 Phase 4/5: end-to-end candidate-generation -> field-classifier
pipeline evaluation + hard-case visual analysis, on the frozen held-out TEST
IMAGE split only (tier2_field_dataset.py's image_split["test"]).

    external image
      -> adaptive_threshold_candidates_v2 (M1.1 v2, unchanged)
      -> candidate crops
      -> field_classifier.onnx (via onnxruntime, the same artifact a
         production integration would load -- not the in-memory training
         model, so this also re-exercises the ONNX path end to end)
      -> predicted class
      -> compared against ground truth

This is the ONLY place that reconstructs, per source image, which
ground-truth boxes got a matching candidate at all (tier2_field_dataset.py's
materialized crops only ever include candidates that exist -- a GT box with
ZERO matching candidates has no row there) -- necessary to separate
candidate-generation misses from classifier mistakes, per the M2 spec.

ISOLATED FROM PRODUCTION: read-only against the frozen annotation set;
writes only under
app/eval/tier2_data/external_monitor_video/tier2_m2_report/.

Usage:
    python -m app.eval.tier2_field_pipeline_eval
"""

import argparse
import json
import os
from typing import Dict, List, Optional

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont

from app.eval.harness import load_dataset
from app.eval.tier2_candidates import adaptive_threshold_candidates_v2
from app.eval.tier2_common import VITALS, Box, iou, warp_box
from app.eval.tier2_field_dataset import CLASSES, NOT_A_VITAL, POS_IOU_THRESHOLD, _letterbox_gray
from app.pipeline.detect import detect_screen

DEFAULT_DATASET_DIR = "app/eval/tier2_data/external_monitor_video"
DEFAULT_FIELD_DATASET_DIR = os.path.join(DEFAULT_DATASET_DIR, "tier2_field_dataset")
DEFAULT_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")

COLOR_GT = (57, 255, 20)  # lime green
COLOR_CORRECT = (0, 200, 255)  # cyan: candidate matched a GT box AND classifier got the vital right
COLOR_WRONG_VITAL = (255, 60, 200)  # magenta: candidate matched a GT box but classifier predicted a different (or no) vital
COLOR_FP_CORRECTLY_REJECTED = (120, 120, 120)  # grey: true negative, correctly predicted not_a_vital (drawn thin/sparse for legibility)
COLOR_FALSE_ALARM = (255, 40, 40)  # red: true negative but classifier predicted a vital class (a classifier-caused false positive)


class FieldClassifier:
    """Thin onnxruntime wrapper -- exercises the SAME artifact
    models/field_classifier.onnx a production integration would load,
    mirroring app.pipeline.onnx_engine.OnnxDigitEngine's own pattern."""

    def __init__(self, models_dir: str = DEFAULT_MODELS_DIR):
        onnx_path = os.path.join(models_dir, "field_classifier.onnx")
        labels_path = os.path.join(models_dir, "field_classifier.labels.json")
        if not os.path.isfile(onnx_path):
            raise SystemExit(f"{onnx_path} not found -- run tier2_field_classifier.py first.")
        with open(labels_path) as f:
            self.labels: List[str] = json.load(f)
        self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])

    def classify(self, gray_crops: List[np.ndarray]) -> List[Dict]:
        if not gray_crops:
            return []
        batch = np.stack([_letterbox_gray(c) for c in gray_crops]).astype(np.float32) / 255.0
        batch = batch[:, None, :, :]
        (logits,) = self.session.run(None, {"crop": batch})
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = exp / exp.sum(axis=1, keepdims=True)
        top_idx = probs.argmax(axis=1)
        return [
            {"label": self.labels[i], "confidence": float(probs[n, i]), "probs": probs[n].tolist()}
            for n, i in enumerate(top_idx)
        ]


def _gt_boxes_for_sample(label: dict, screen) -> Dict[str, Box]:
    out: Dict[str, Box] = {}
    for vital in VITALS:
        raw = label.get("rois", {}).get(vital)
        if not raw or raw[2] <= 0 or raw[3] <= 0:
            continue
        box: Box = tuple(raw)
        if screen.detected and screen.homography is not None:
            box = warp_box(box, screen.homography)
        out[vital] = box
    return out


def evaluate_image(png_path: str, label: dict, classifier: FieldClassifier) -> dict:
    import cv2

    img = np.array(Image.open(png_path).convert("RGB"))
    screen = detect_screen(img)
    work_img = screen.image
    gray_full = cv2.cvtColor(work_img, cv2.COLOR_RGB2GRAY)
    h, w = gray_full.shape[:2]

    gt_boxes = _gt_boxes_for_sample(label, screen)
    candidates = adaptive_threshold_candidates_v2(work_img)

    crops = []
    valid_boxes = []
    for box in candidates:
        x, y, bw, bh = box
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
        if x1 <= x0 or y1 <= y0:
            continue
        crops.append(gray_full[y0:y1, x0:x1])
        valid_boxes.append(box)

    preds = classifier.classify(crops)

    candidate_records = []
    for box, pred in zip(valid_boxes, preds):
        best_vital, best_iou_val = None, 0.0
        for vital, gt in gt_boxes.items():
            v = iou(box, gt)
            if v > best_iou_val:
                best_iou_val = v
                best_vital = vital
        true_label = best_vital if (best_vital is not None and best_iou_val >= POS_IOU_THRESHOLD) else NOT_A_VITAL
        candidate_records.append(
            {
                "box": box,
                "true_label": true_label,
                "best_iou": best_iou_val,
                "matched_vital": best_vital,
                "pred_label": pred["label"],
                "pred_confidence": pred["confidence"],
            }
        )

    # Per-GT-box: was it found by ANY candidate at IoU>=threshold, and if so
    # was the BEST-IoU such candidate classified correctly? This is
    # reconstructed independently of tier2_field_dataset.py's materialized
    # crops (which only ever contain rows for candidates that exist) so a
    # GT box with ZERO matching candidates is counted as a candidate-miss,
    # not silently absent.
    gt_results = {}
    for vital, gt in gt_boxes.items():
        matches = [r for r in candidate_records if r["matched_vital"] == vital and r["best_iou"] >= POS_IOU_THRESHOLD]
        if not matches:
            gt_results[vital] = {"candidate_found": False, "classified_correctly": False, "reason": "candidate_generation_miss"}
            continue
        best = max(matches, key=lambda r: r["best_iou"])
        correct = best["pred_label"] == vital
        gt_results[vital] = {
            "candidate_found": True,
            "classified_correctly": correct,
            "reason": "correct" if correct else f"classifier_predicted_{best['pred_label']}",
            "best_iou": best["best_iou"],
            "pred_confidence": best["pred_confidence"],
        }

    return {
        "id": label.get("id"),
        "screen_detected": screen.detected,
        "candidates": candidate_records,
        "gt_results": gt_results,
    }


def aggregate(results: List[dict]) -> dict:
    # Candidate recall / end-to-end accuracy, per vital, over GT boxes.
    per_vital = {v: {"n": 0, "candidate_found": 0, "end_to_end_correct": 0} for v in VITALS}
    for r in results:
        for vital, gr in r["gt_results"].items():
            per_vital[vital]["n"] += 1
            if gr["candidate_found"]:
                per_vital[vital]["candidate_found"] += 1
            if gr["classified_correctly"]:
                per_vital[vital]["end_to_end_correct"] += 1

    def _rate(num, den):
        return (num / den) if den else None

    per_vital_summary = {
        v: {
            "n": d["n"],
            "candidate_recall": _rate(d["candidate_found"], d["n"]),
            "end_to_end_accuracy": _rate(d["end_to_end_correct"], d["n"]),
            # Of the ones the candidate generator actually found, how often
            # did the classifier get it right -- isolates classifier error
            # from candidate-generation error.
            "classifier_accuracy_given_candidate_found": _rate(d["end_to_end_correct"], d["candidate_found"]),
        }
        for v, d in per_vital.items()
    }

    total_n = sum(d["n"] for d in per_vital.values())
    total_found = sum(d["candidate_found"] for d in per_vital.values())
    total_correct = sum(d["end_to_end_correct"] for d in per_vital.values())

    # Classifier precision/recall over ALL individual candidates (the
    # classification task itself, independent of the GT-box view above).
    all_candidates = [c for r in results for c in r["candidates"]]
    classes = CLASSES
    cm = {t: {p: 0 for p in classes} for t in classes}
    for c in all_candidates:
        cm[c["true_label"]][c["pred_label"]] += 1

    per_class = {}
    for c in classes:
        tp = cm[c][c]
        support = sum(cm[c].values())
        pred_count = sum(cm[t][c] for t in classes)
        fp = pred_count - tp
        precision = tp / pred_count if pred_count else None
        recall = tp / support if support else None
        per_class[c] = {"support": support, "precision": precision, "recall": recall, "predicted_count": pred_count}

    not_a_vital_recall = per_class[NOT_A_VITAL]["recall"]  # == false-positive rejection rate

    return {
        "n_test_images": len(results),
        "n_candidates_total": len(all_candidates),
        "per_vital": per_vital_summary,
        "overall_candidate_recall": _rate(total_found, total_n),
        "overall_end_to_end_accuracy": _rate(total_correct, total_n),
        "classifier_confusion_matrix": cm,
        "classifier_per_class": per_class,
        "false_positive_rejection_rate": not_a_vital_recall,
    }


def render_debug_image_with_gt(png_path: str, label: dict, result: dict, out_path: str) -> None:
    img = np.array(Image.open(png_path).convert("RGB"))
    screen = detect_screen(img)
    canvas = Image.fromarray(screen.image).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    for vital in VITALS:
        raw = label.get("rois", {}).get(vital)
        if not raw or raw[2] <= 0 or raw[3] <= 0:
            continue
        box: Box = tuple(raw)
        if screen.detected and screen.homography is not None:
            box = warp_box(box, screen.homography)
        x, y, bw, bh = box
        draw.rectangle([x, y, x + bw, y + bh], outline=COLOR_GT, width=3)
        draw.text((x, max(0, y - 26)), f"gt:{vital}", fill=COLOR_GT)

    for c in result["candidates"]:
        x, y, bw, bh = c["box"]
        if c["true_label"] != NOT_A_VITAL:
            color = COLOR_CORRECT if c["pred_label"] == c["true_label"] else COLOR_WRONG_VITAL
            width = 3
        else:
            if c["pred_label"] == NOT_A_VITAL:
                color = COLOR_FP_CORRECTLY_REJECTED
                width = 1
            else:
                color = COLOR_FALSE_ALARM
                width = 3
        draw.rectangle([x, y, x + bw, y + bh], outline=color, width=width)
        if c["true_label"] != NOT_A_VITAL or c["pred_label"] != NOT_A_VITAL:
            tag = f"{c['pred_label']}:{c['pred_confidence']*100:.0f}%"
            draw.text((x, max(0, y - 12)), tag, fill=color)

    draw.text((6, 6), "green=GT  cyan=correct  magenta=wrong-vital  red=classifier false-alarm  grey=correctly-rejected", fill=(255, 255, 0))
    canvas.save(out_path)


def render_text_summary(summary: dict) -> str:
    lines = []
    lines.append(f"Test images: {summary['n_test_images']}   total candidates: {summary['n_candidates_total']}")
    lines.append("")
    lines.append("Per-vital (GT-box view): n, candidate_recall, end_to_end_accuracy, classifier_accuracy_given_found")
    for v in VITALS:
        d = summary["per_vital"][v]

        def _p(x):
            return f"{x*100:.1f}%" if x is not None else "n/a"

        lines.append(f"  {v:8s} n={d['n']:3d}  recall={_p(d['candidate_recall']):>7s}  e2e_acc={_p(d['end_to_end_accuracy']):>7s}  clf_acc|found={_p(d['classifier_accuracy_given_candidate_found']):>7s}")
    lines.append("")
    lines.append(f"Overall candidate recall (test images): {summary['overall_candidate_recall']*100:.1f}%")
    lines.append(f"Overall end-to-end accuracy (test images): {summary['overall_end_to_end_accuracy']*100:.1f}%")
    lines.append(f"False-positive rejection rate (not_a_vital recall): {summary['false_positive_rejection_rate']*100:.1f}%")
    lines.append("")
    lines.append("Classifier per-class (all candidates in test images):")
    for c, d in summary["classifier_per_class"].items():
        p = f"{d['precision']*100:.1f}%" if d["precision"] is not None else "n/a"
        r = f"{d['recall']*100:.1f}%" if d["recall"] is not None else "n/a"
        lines.append(f"  {c:12s} support={d['support']:4d}  precision={p:>7s}  recall={r:>7s}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=DEFAULT_DATASET_DIR)
    parser.add_argument("--field-dataset-dir", default=DEFAULT_FIELD_DATASET_DIR)
    parser.add_argument("--models-dir", default=DEFAULT_MODELS_DIR)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    with open(os.path.join(args.field_dataset_dir, "report.json")) as f:
        field_report = json.load(f)
    test_ids = set(field_report["image_split"]["test"])

    out_dir = args.out or os.path.join(args.dataset, "tier2_m2_report")
    debug_dir = os.path.join(out_dir, "debug")
    os.makedirs(debug_dir, exist_ok=True)

    samples = load_dataset(args.dataset)
    test_samples = [s for s in samples if s["id"] in test_ids]
    print(f"Evaluating end-to-end pipeline on {len(test_samples)} held-out TEST images: {sorted(test_ids)}")

    classifier = FieldClassifier(args.models_dir)

    results = []
    for s in test_samples:
        r = evaluate_image(s["png_path"], s["label"], classifier)
        results.append(r)
        debug_path = os.path.join(debug_dir, f"debug_{r['id']}.png")
        render_debug_image_with_gt(s["png_path"], s["label"], r, debug_path)

    summary = aggregate(results)
    summary_text = render_text_summary(summary)
    print()
    print(summary_text)

    out_json = os.path.join(out_dir, "tier2_m2_report.json")

    def _default(o):
        if isinstance(o, (tuple, list)):
            return list(o)
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        return str(o)

    with open(out_json, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, default=_default)
    out_txt = os.path.join(out_dir, "tier2_m2_report.txt")
    with open(out_txt, "w") as f:
        f.write(summary_text + "\n")

    print()
    print(f"Wrote {out_json}")
    print(f"Wrote {out_txt}")
    print(f"Wrote {len(results)} debug overlays to {debug_dir}")


if __name__ == "__main__":
    main()
