"""M5: zero-shot generalization benchmark -- does the exact, unmodified M4.6
production Tier-2 pipeline (adaptive_threshold_candidates_v2 -> FieldCNN
[models/field_classifier.onnx] -> per-vital selection -> production
TesseractEngine -> reconcile()) generalize to a SECOND anaesthesia monitor
(GE CARESCAPE B650, external_real_world_monitor_video, 17 frames) that was
never used to build or tune any of those artifacts?

ZERO-SHOT: nothing here retrains, fine-tunes, or reconfigures the model,
thresholds, candidate generator, OCR config, or reconcile rules. Every
production building block is imported and called exactly as
app.pipeline.read_frame.read_frame() / app.pipeline.tier2_roi.
extract_rois_by_field_classifier() call it -- this script only adds
instrumentation (recording each intermediate stage's output) so failures can
be attributed to a specific pipeline stage (candidate generation / FieldCNN
classification / candidate selection / OCR / reconcile), which is what M5
Phase 6/7 asks for and no single existing M1-M4 script already produces
end-to-end.

Reused, unmodified:
  - app.eval.harness.load_dataset -- same sample_XXXX.png/json loader as
    every prior milestone.
  - app.eval.tier2_candidates.adaptive_threshold_candidates_v2 -- the exact
    production candidate generator (imported by app.pipeline.tier2_roi).
  - app.pipeline.field_classifier.get_default_field_classifier /
    FieldClassifierEngine -- the exact production ONNX FieldCNN wrapper.
  - app.pipeline.tier2_roi.extract_rois_by_field_classifier,
    MIN_CLASSIFIER_CONFIDENCE, DEDUPE_IOU, COMPETING_MARGIN -- the exact
    production selection stage and its thresholds, unmodified.
  - app.eval.tier2_field_dataset.POS_IOU_THRESHOLD, NOT_A_VITAL -- same
    0.3 IoU "found" convention M1-M3 already established.
  - app.eval.m4_3_reliability.run_variant / replay_reconcile -- the same
    real-pipeline-plus-real-reconcile() harness M4.3/M4.5/M4.6 used, called
    here with the real production TesseractEngine (no eval subclass) exactly
    as M4.6's own reproduction script did.
  - app.eval.m4_3_analysis.field_summary / _category_d_mask-style aggregation
    idiom, adapted for this dataset's fields.

Usage:
    python -m app.eval.m5_second_monitor_generalization
"""

import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.eval.harness import load_dataset
from app.eval.tier2_candidates import adaptive_threshold_candidates_v2
from app.eval.tier2_common import VITALS, Box, iou, warp_box
from app.eval.tier2_field_dataset import CLASSES, NOT_A_VITAL, POS_IOU_THRESHOLD
from app.pipeline.detect import detect_screen
from app.pipeline.field_classifier import FieldClassifierEngine, get_default_field_classifier
from app.pipeline.tier2_roi import (
    COMPETING_MARGIN,
    DEDUPE_IOU,
    MIN_CLASSIFIER_CONFIDENCE,
    extract_rois_by_field_classifier,
)

# m4_3_reliability sets ROI_ENGINE=tier2 (setdefault) on import and locates the
# real tesseract binary -- reused unmodified, same as M4.6's own m4_6_production_promotion.py.
from app.eval.m4_3_reliability import FIELDS, FIELD_TO_GROUP, SCORED_FIELDS, replay_reconcile, run_variant
from app.pipeline.ocr import TesseractEngine

DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tier2_data", "external_monitor_B")
GT_PATH = os.path.join(DATASET_DIR, "m5_ground_truth_values.json")
OUT_DIR = os.path.join(DATASET_DIR, "m5_report")
DEBUG_DIR = os.path.join(OUT_DIR, "failure_overlays")

FIELD_DECIMALS = {"hr": 0, "spo2": 0, "nibpSystolic": 0, "nibpDiastolic": 0, "nibpMean": 0, "etco2": 0, "temp": 1, "rr": 0}

COLOR_GT = (57, 255, 20)
COLOR_CANDIDATE = (120, 120, 120)
COLOR_SELECTED = (0, 200, 255)
COLOR_WRONG = (255, 60, 200)


def _round_display(value, decimals):
    if value is None:
        return None
    return round(value, decimals) if decimals else float(round(value))


def _gt_for_sample(gt_all: dict, sample_id: str) -> Dict[str, Optional[float]]:
    raw = gt_all.get(sample_id, {})
    out: Dict[str, Optional[float]] = {f: None for f in FIELDS}
    for k, v in raw.items():
        if k == "nibp":
            parts = str(v).split("/")
            out["nibpSystolic"] = float(parts[0])
            out["nibpDiastolic"] = float(parts[1])
            if len(parts) > 2:
                out["nibpMean"] = float(parts[2])
        else:
            out[k] = float(v)
    return out


# ─── Phase 6A/6B/6C: candidate generation + FieldCNN + selection, instrumented ──


def evaluate_stage_abc(png_path: str, label: dict, classifier: FieldClassifierEngine) -> dict:
    """One image: candidate generation -> FieldCNN classification -> the
    REAL production selection stage (extract_rois_by_field_classifier,
    called directly, not reimplemented) -- returns enough detail per GT vital
    to classify A (no candidate) / B (candidate existed, misclassified) /
    C (correctly classified candidate existed, but a different/no box was
    selected) vs. "reached OCR cleanly"."""
    img = np.array(Image.open(png_path).convert("RGB"))
    screen = detect_screen(img)
    work_img = screen.image
    h, w = work_img.shape[:2]

    gt_boxes: Dict[str, Box] = {}
    for vital in VITALS:
        raw = label.get("rois", {}).get(vital)
        if not raw or raw[2] <= 0 or raw[3] <= 0:
            continue
        box: Box = tuple(raw)
        if screen.detected and screen.homography is not None:
            box = warp_box(box, screen.homography)
        gt_boxes[vital] = box

    raw_candidates = adaptive_threshold_candidates_v2(work_img)
    boxes: List[Tuple[int, int, int, int]] = []
    crops: List[np.ndarray] = []
    for box in raw_candidates:
        x, y, bw, bh = box
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
        if x1 <= x0 or y1 <= y0:
            continue
        crops.append(work_img[y0:y1, x0:x1])
        boxes.append((x0, y0, x1 - x0, y1 - y0))

    preds = classifier.classify(crops)

    candidate_records = []
    for box, pred in zip(boxes, preds):
        best_vital, best_iou_val = None, 0.0
        for vital, gt in gt_boxes.items():
            v = iou(box, gt)
            if v > best_iou_val:
                best_iou_val = v
                best_vital = vital
        true_label = best_vital if (best_vital is not None and best_iou_val >= POS_IOU_THRESHOLD) else NOT_A_VITAL
        candidate_records.append(
            {"box": box, "true_label": true_label, "best_iou": best_iou_val, "pred_label": pred.label, "pred_confidence": pred.confidence}
        )

    # The REAL production selection stage, called directly (not reimplemented).
    selected = extract_rois_by_field_classifier(work_img, classifier=classifier)

    gt_stage_results = {}
    for vital, gt_box in gt_boxes.items():
        matches = [c for c in candidate_records if c["true_label"] == vital and c["best_iou"] >= POS_IOU_THRESHOLD]
        candidate_found = len(matches) > 0
        correctly_classified = any(
            c["pred_label"] == vital and c["pred_confidence"] >= MIN_CLASSIFIER_CONFIDENCE for c in matches
        )
        sel = selected.get(vital)
        selection_iou = iou(sel.box, gt_box) if sel is not None else 0.0
        selection_correct = sel is not None and selection_iou >= POS_IOU_THRESHOLD
        gt_stage_results[vital] = {
            "candidate_found": candidate_found,
            "n_matching_candidates": len(matches),
            "correctly_classified": correctly_classified,
            "best_candidate_confidence": max((c["pred_confidence"] for c in matches), default=None),
            "production_selected_box": sel.box if sel is not None else None,
            "production_selection_iou": selection_iou,
            "production_selection_correct": selection_correct,
        }

    return {
        "id": label.get("id"),
        "screen_detected": screen.detected,
        "gt_boxes": gt_boxes,
        "candidates": candidate_records,
        "gt_stage_results": gt_stage_results,
        "n_candidates_total": len(candidate_records),
    }


def classify_failure(stage_abc_entry: Optional[dict], field_entry: dict) -> str:
    """Combine the candidate/classifier/selection stage result with the
    OCR+reconcile timeline entry (from replay_reconcile) into one of:
    A (candidate-gen miss), B (misclassified), C (wrong crop selected),
    D (OCR wrong on a correctly-selected crop), E (OCR correct but
    reconcile produced a wrong/rejected final state), or "correct" (no
    failure at any stage)."""
    if field_entry["gt"] is None:
        return "unscored"
    if stage_abc_entry is None:
        # nibp fields have no per-vital stage entry structure here (nibp
        # never appears in this dataset) -- fall back to OCR/reconcile only.
        if field_entry["ocr_class"] == "missing":
            return "D_ocr_missing"
        if field_entry["ocr_class"] == "wrong":
            return "D_ocr_wrong"
        if field_entry["confirmed_correct"] is False:
            return "E_reconcile_rejected_correct_ocr"
        return "correct"

    if not stage_abc_entry["candidate_found"]:
        return "A_candidate_generation_miss"
    if not stage_abc_entry["correctly_classified"]:
        return "B_classifier_misclassified"
    if not stage_abc_entry["production_selection_correct"]:
        return "C_wrong_crop_selected"
    if field_entry["ocr_class"] == "missing":
        return "D_ocr_missing"
    if field_entry["ocr_class"] == "wrong":
        return "D_ocr_wrong"
    if field_entry["confirmed_correct"] is False:
        return "E_reconcile_rejected_correct_ocr"
    if field_entry["confirmed_correct"] is True:
        return "correct"
    return "unscored"


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)

    with open(GT_PATH) as f:
        gt_doc = json.load(f)
    gt_all = gt_doc["values"]

    samples = load_dataset(DATASET_DIR)
    print(f"Loaded {len(samples)} samples from {DATASET_DIR}")
    assert len(samples) == 17, f"expected 17 samples, got {len(samples)}"

    classifier = get_default_field_classifier()
    print(f"Loaded production FieldCNN: {classifier.session.get_inputs()[0].name}, classes={classifier.labels}")

    # ── Stage A/B/C: candidate generation + FieldCNN + real selection ──
    print("\n=== Stage A/B/C: candidate generation -> FieldCNN -> production selection ===")
    stage_abc_by_id: Dict[str, dict] = {}
    for s in samples:
        r = evaluate_stage_abc(s["png_path"], s["label"], classifier)
        stage_abc_by_id[s["id"]] = r
        print(f"  {s['id']}: {r['n_candidates_total']} candidates, screen_detected={r['screen_detected']}")

    # ── Stage D/E/F: real production TesseractEngine + real reconcile() ──
    print("\n=== Stage D/E/F: production TesseractEngine (M4.6, unmodified) + reconcile() ===")
    production_engine = TesseractEngine()
    records = run_variant(samples, gt_all, "M5_PRODUCTION", production_engine)

    with open(os.path.join(OUT_DIR, "m5_raw_records.json"), "w") as f:
        json.dump(records, f, indent=2, default=str)

    timeline = replay_reconcile(records, interval_ms=1000)
    with open(os.path.join(OUT_DIR, "m5_timeline_interval1000.json"), "w") as f:
        json.dump(timeline, f, indent=2, default=str)

    # ── Combine into the failure taxonomy, per (sample, vital/field) ──
    print("\n=== Building failure taxonomy ===")
    taxonomy_records = []
    for frame in timeline:
        sid = frame["id"]
        stage_abc = stage_abc_by_id.get(sid)
        for field in FIELDS:
            group = FIELD_TO_GROUP[field]
            entry = frame["fields"][field]
            stage_entry = stage_abc["gt_stage_results"].get(group) if (stage_abc and group != "nibp") else None
            category = classify_failure(stage_entry, entry)
            taxonomy_records.append({"id": sid, "field": field, "vital_group": group, "category": category, **entry})

    with open(os.path.join(OUT_DIR, "m5_failure_taxonomy.json"), "w") as f:
        json.dump(taxonomy_records, f, indent=2, default=str)

    # ── Aggregate: per-vital-group candidate recall / classifier accuracy ──
    per_group_stage: Dict[str, dict] = {v: {"n": 0, "candidate_found": 0, "correctly_classified": 0, "selection_correct": 0} for v in VITALS}
    for sid, r in stage_abc_by_id.items():
        for vital, gr in r["gt_stage_results"].items():
            per_group_stage[vital]["n"] += 1
            if gr["candidate_found"]:
                per_group_stage[vital]["candidate_found"] += 1
            if gr["correctly_classified"]:
                per_group_stage[vital]["correctly_classified"] += 1
            if gr["production_selection_correct"]:
                per_group_stage[vital]["selection_correct"] += 1

    def _rate(num, den):
        return (num / den) if den else None

    stage_summary = {
        v: {
            "n_gt_present": d["n"],
            "candidate_recall": _rate(d["candidate_found"], d["n"]),
            "classifier_accuracy_given_candidate_found": _rate(d["correctly_classified"], d["candidate_found"]),
            "end_to_end_classification_accuracy": _rate(d["correctly_classified"], d["n"]),
            "production_selection_accuracy": _rate(d["selection_correct"], d["n"]),
        }
        for v, d in per_group_stage.items()
    }

    # ── Aggregate: per-field OCR / confirmed accuracy (M4.3-style field_summary, adapted) ──
    def field_summary(field: str) -> dict:
        entries = [fr["fields"][field] for fr in timeline]
        scored = [e for e in entries if e["gt"] is not None]
        n = len(scored)
        ocr_correct = sum(1 for e in scored if e["ocr_class"] == "correct")
        ocr_wrong = sum(1 for e in scored if e["ocr_class"] == "wrong")
        ocr_missing = sum(1 for e in scored if e["ocr_class"] == "missing")
        confirmed_correct = sum(1 for e in scored if e["confirmed_correct"] is True)
        confirmed_wrong = sum(1 for e in scored if e["confirmed_correct"] is False)
        reject_reasons: Dict[str, int] = {}
        for e in scored:
            if e["ocr_class"] == "wrong" and not e["accepted"]:
                reject_reasons[e["reason"]] = reject_reasons.get(e["reason"], 0) + 1
        confidences = [e["fused_confidence"] for e in scored]
        return {
            "n_gt_present": n,
            "ocr_correct": ocr_correct,
            "ocr_wrong": ocr_wrong,
            "ocr_missing": ocr_missing,
            "ocr_accuracy": _rate(ocr_correct, n),
            "parse_failure_rate": _rate(ocr_missing, n),
            "wrong_read_rate": _rate(ocr_wrong, n),
            "confirmed_correct": confirmed_correct,
            "confirmed_wrong": confirmed_wrong,
            "confirmed_accuracy": _rate(confirmed_correct, n),
            "wrong_ocr_reject_reasons": reject_reasons,
            "confidence_mean": (sum(confidences) / len(confidences)) if confidences else None,
            "confidence_min": min(confidences) if confidences else None,
            "confidence_max": max(confidences) if confidences else None,
        }

    field_level_summary = {field: field_summary(field) for field in FIELDS}

    # micro (all scored fields pooled)
    total_n = sum(v["n_gt_present"] for v in field_level_summary.values())
    total_ocr_correct = sum(v["ocr_correct"] for v in field_level_summary.values())
    total_confirmed_correct = sum(v["confirmed_correct"] for v in field_level_summary.values())
    micro = {
        "total_scored_fields": total_n,
        "micro_ocr_accuracy": _rate(total_ocr_correct, total_n),
        "micro_confirmed_accuracy": _rate(total_confirmed_correct, total_n),
    }

    # failure taxonomy counts, per vital group
    from collections import Counter

    taxonomy_counts: Dict[str, Counter] = {}
    for rec in taxonomy_records:
        if rec["category"] == "unscored":
            continue
        taxonomy_counts.setdefault(rec["vital_group"], Counter())[rec["category"]] += 1

    summary = {
        "dataset": "external_monitor_B",
        "n_images": len(samples),
        "stage_abc_candidate_classifier_selection": stage_summary,
        "field_level": field_level_summary,
        "micro": micro,
        "failure_taxonomy_counts": {k: dict(v) for k, v in taxonomy_counts.items()},
    }
    with open(os.path.join(OUT_DIR, "m5_analysis_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\n=== Stage A/B/C summary (candidate recall / classifier / selection) ===")
    for v in VITALS:
        d = stage_summary[v]
        print(f"  {v:8s} n={d['n_gt_present']:3d}  cand_recall={d['candidate_recall']}  clf_acc|found={d['classifier_accuracy_given_candidate_found']}  sel_acc={d['production_selection_accuracy']}")

    print("\n=== Field-level OCR / confirmed accuracy ===")
    for field in FIELDS:
        d = field_level_summary[field]
        print(f"  {field:16s} n={d['n_gt_present']:3d}  ocr_acc={d['ocr_accuracy']}  confirmed_acc={d['confirmed_accuracy']}")

    print(f"\nMicro OCR accuracy: {micro['micro_ocr_accuracy']}")
    print(f"Micro confirmed accuracy: {micro['micro_confirmed_accuracy']}")

    print("\n=== Failure taxonomy (per vital group) ===")
    for v, counts in taxonomy_counts.items():
        print(f"  {v}: {dict(counts)}")

    print(f"\nWrote {OUT_DIR}/m5_analysis_summary.json, m5_failure_taxonomy.json, m5_raw_records.json, m5_timeline_interval1000.json")


if __name__ == "__main__":
    main()
