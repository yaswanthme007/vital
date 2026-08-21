"""M5.4.1 Phase 4/5: does the crop-integrity signal (app.validation.
crop_integrity.has_residual_content) actually prevent the exact held-out
failure M5.4 found, without costing more than it buys elsewhere?

Unlike app.eval.m5_4_confirmation_eval (which replays M5.3's ALREADY-
COMPUTED ocr_records), this script re-runs real OCR itself, because M5.3's
records only carry (value, confidence, correct) -- not the raw recognized
text app.validation.crop_integrity needs. Everything else is reused
unmodified: app.eval.m5_3_tracking_eval's sample loading / calibration-
profile construction / dataset paths, the REAL production
app.pipeline.calibrated_roi.make_extractor + app.pipeline.layout_tracker.
LayoutTracker, and the REAL app.pipeline.ocr.TesseractEngine (via
read_vital_with_diagnostics, M5.4.1's only new OCR-layer surface, which
read_vital itself still delegates to unchanged -- see ocr.py).

Three reconcile() arms are compared on IDENTICAL per-tick data, so the
delta between them isolates exactly one variable each time:

  baseline        temporal_state=None -- pre-M5.4 behaviour (the hard
                  floor: M5.4.1 must never regress below this)
  m5_4            temporal_state set, per_vital_crop_suspicious=None --
                  M5.4's original (shipped-off) mechanism, reproducing its
                  own reported numbers including the frozen_B[sample_0011]
                  regression
  m5_4_1          temporal_state set, per_vital_crop_suspicious populated
                  from real OCR diagnostics -- this milestone's fix

Usage:
    python -m app.eval.m5_4_1_crop_integrity_eval
"""

import json
import os
from typing import Dict, List

import numpy as np
from PIL import Image

from app.eval.m5_2_calibration_eval import FIELD_DECIMALS, FIELD_TO_VITAL, FIELDS, VITALS, _round_display
from app.eval.m5_3_tracking_eval import (
    ANCHOR_DIR,
    ANCHOR_GT,
    DATASET_B_EXCLUDE,
    FROZEN_A_DIR,
    FROZEN_A_GT,
    FROZEN_B_DIR,
    FROZEN_B_GT,
    _gt_values,
    _load_samples,
    build_single_frame_profile,
)
from app.pipeline.calibrated_roi import make_extractor, reference_pixel_boxes
from app.pipeline.layout_tracker import LayoutTracker
from app.pipeline.ocr import NibpValue, TesseractEngine, _locate_tesseract_binary
from app.validation.crop_integrity import has_residual_content
from app.validation.reconcile import initial_confirmed_state, reconcile
from app.validation.rules import normalize_temp_celsius
from app.validation.temporal import initial_temporal_state

import pytesseract

_resolved = _locate_tesseract_binary(None)
if _resolved:
    pytesseract.pytesseract.tesseract_cmd = _resolved

ENGINE = TesseractEngine()
OUT_DIR = "app/eval/tier2_data/m5_4_1_report"

DATASETS = {
    "frozen_A": (FROZEN_A_DIR, "sample_", FROZEN_A_GT, "sample_0006", set()),
    "frozen_B[sample_0001]": (FROZEN_B_DIR, "sample_", FROZEN_B_GT, "sample_0001", DATASET_B_EXCLUDE),
    "frozen_B[sample_0011]": (FROZEN_B_DIR, "sample_", FROZEN_B_GT, "sample_0011", DATASET_B_EXCLUDE),
    "dense_B_anchors": (ANCHOR_DIR, "anchor_", ANCHOR_GT, "anchor_004971", DATASET_B_EXCLUDE),
}


# --- Phase 4a: re-run real OCR with diagnostics, tracked arm only ---------


def extract_tracked_with_diagnostics(
    directory: str, prefix: str, gt_path: str, reference_id: str, exclude: set
) -> List[dict]:
    with open(gt_path) as f:
        gt_all = json.load(f)["values"]
    samples = _load_samples(directory, prefix)
    ref_sample = next(s for s in samples if s["id"] == reference_id)
    profile, ref_img = build_single_frame_profile(ref_sample)
    tracker = LayoutTracker.from_reference_image(
        ref_img, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )
    extractor = make_extractor(profile, tracker=tracker)

    records: List[dict] = []
    for s in samples:
        if s["id"] == reference_id:
            continue
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        values = _gt_values(gt_all, s["id"])
        rois = extractor(img)

        for vital in VITALS:
            roi = rois.get(vital)
            group = ["nibpSystolic", "nibpDiastolic", "nibpMean"] if vital == "nibp" else [vital]
            if all(values[f] is None for f in group):
                continue

            if roi is None:
                parsed = {f: None for f in group}
                confidence, suspicious = 0.0, False
            else:
                value, confidence, diag = ENGINE.read_vital_with_diagnostics(roi.crop, vital)
                suspicious = has_residual_content(diag.raw_text, diag.matched_text)
                if vital == "nibp":
                    parsed = (
                        {"nibpSystolic": value.systolic, "nibpDiastolic": value.diastolic, "nibpMean": value.mean}
                        if isinstance(value, NibpValue) else {f: None for f in group}
                    )
                else:
                    parsed = {vital: value}

            for fld in group:
                gt = values[fld]
                if gt is None:
                    continue
                dec = FIELD_DECIMALS[fld]
                pred_v = _round_display(parsed[fld], dec)
                excluded = fld in exclude or FIELD_TO_VITAL[fld] in exclude
                records.append({
                    "id": s["id"], "vital": vital, "field": fld,
                    "ground_truth": _round_display(gt, dec), "predicted": pred_v,
                    "missing": parsed[fld] is None,
                    "correct": None if excluded else (parsed[fld] is not None and pred_v == _round_display(gt, dec)),
                    "excluded_from_scoring": excluded,
                    "confidence": confidence,
                    "crop_suspicious": bool(suspicious) if roi is not None else False,
                })
    return records


# --- Phase 4b: replay reconcile() three ways on identical ticks -----------


def replay(records: List[dict], mode: str, interval_ms: int = 1000, base_ts: int = 1_700_000_000_000) -> dict:
    """mode: 'baseline' (temporal off), 'm5_4' (temporal on, no crop-integrity
    gate), 'm5_4_1' (temporal on, crop-integrity gate active)."""
    assert mode in ("baseline", "m5_4", "m5_4_1")
    by_id: Dict[str, Dict[str, dict]] = {}
    for r in records:
        by_id.setdefault(r["id"], {})[r["field"]] = r
    sample_ids = sorted(by_id.keys())

    confirmed = initial_confirmed_state(base_ts)
    temporal_state = initial_temporal_state() if mode != "baseline" else None

    field_stats = {f: {"n": 0, "ocr_correct": 0, "confirmed_correct": 0, "wrong_but_confirmed": 0,
                        "temporal_corroborations": 0, "temporal_corroborations_correct": 0} for f in FIELDS}
    corroboration_examples: List[dict] = []

    for i, sid in enumerate(sample_ids):
        ts = base_ts + i * interval_ms
        field_records = by_id[sid]

        raw_reading: dict = {"timestamp": ts}
        per_vital_confidence: Dict[str, float] = {}
        per_vital_crop_suspicious: Dict[str, bool] = {}
        for field in FIELDS:
            rec = field_records.get(field)
            raw_reading[field] = rec["predicted"] if rec else None
            if rec is not None:
                vital = FIELD_TO_VITAL[field]
                per_vital_confidence[vital] = rec["confidence"]
                per_vital_crop_suspicious[vital] = rec["crop_suspicious"]

        reading, confirmed, flagged = reconcile(
            raw_reading, per_vital_confidence, confirmed, temporal_state=temporal_state,
            per_vital_crop_suspicious=(per_vital_crop_suspicious if mode == "m5_4_1" else None),
        )

        for field in FIELDS:
            rec = field_records.get(field)
            if rec is None or rec["excluded_from_scoring"]:
                continue
            gt = rec["ground_truth"]
            if gt is None:
                continue
            dec = FIELD_DECIMALS[field]
            gt_disp = _round_display(normalize_temp_celsius(gt), dec) if field == "temp" else gt
            confirmed_disp = _round_display(reading[field], dec)
            raw_disp = (_round_display(normalize_temp_celsius(rec["predicted"]), dec)
                        if field == "temp" and rec["predicted"] is not None else rec["predicted"])

            st = field_stats[field]
            st["n"] += 1
            if rec["correct"]:
                st["ocr_correct"] += 1
            confirmed_correct = confirmed_disp == gt_disp
            if confirmed_correct:
                st["confirmed_correct"] += 1
            if (not rec["correct"]) and (not confirmed_correct) and raw_disp is not None and confirmed_disp == raw_disp:
                st["wrong_but_confirmed"] += 1

        for f in flagged:
            for part in f.get("frameNote", "").split("; "):
                if "temporal corroboration" not in part:
                    continue
                field = next((fld for fld in FIELDS if part.startswith(fld + ":")), None)
                st = field_stats.get(field)
                rec = field_records.get(field) if field else None
                if st is not None:
                    st["temporal_corroborations"] += 1
                    correct = bool(rec and rec["correct"])
                    if correct:
                        st["temporal_corroborations_correct"] += 1
                    corroboration_examples.append({
                        "mode": mode, "id": sid, "field": field,
                        "predicted": rec["predicted"] if rec else None,
                        "ground_truth": rec["ground_truth"] if rec else None,
                        "confidence": rec["confidence"] if rec else None,
                        "crop_suspicious": rec["crop_suspicious"] if rec else None,
                        "ocr_correct": correct,
                    })

    total_n = sum(v["n"] for v in field_stats.values())
    total_ocr_correct = sum(v["ocr_correct"] for v in field_stats.values())
    total_confirmed_correct = sum(v["confirmed_correct"] for v in field_stats.values())
    total_wrong_but_confirmed = sum(v["wrong_but_confirmed"] for v in field_stats.values())
    total_corroborations = sum(v["temporal_corroborations"] for v in field_stats.values())
    total_corroborations_correct = sum(v["temporal_corroborations_correct"] for v in field_stats.values())

    return {
        "mode": mode, "n_scored": total_n,
        "micro_ocr_accuracy": (total_ocr_correct / total_n) if total_n else None,
        "micro_confirmed_accuracy": (total_confirmed_correct / total_n) if total_n else None,
        "confidently_wrong_confirmations": total_wrong_but_confirmed,
        "temporal_corroborations": total_corroborations,
        "temporal_corroborations_correct": total_corroborations_correct,
        "temporal_corroborations_wrong": total_corroborations - total_corroborations_correct,
        "corroboration_examples": corroboration_examples,
        "field_stats": field_stats,
    }


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    all_results = []
    print("=== M5.4.1 Phase 4/5: crop-integrity gate, real OCR, real reconcile() ===\n")
    for dataset, (directory, prefix, gt_path, reference_id, exclude) in DATASETS.items():
        records = extract_tracked_with_diagnostics(directory, prefix, gt_path, reference_id, exclude)
        n_suspicious = sum(1 for r in records if r["crop_suspicious"] and not r["excluded_from_scoring"])
        baseline = replay(records, "baseline")
        m5_4 = replay(records, "m5_4")
        m5_4_1 = replay(records, "m5_4_1")
        all_results.append({"dataset": dataset, "n_suspicious_ticks": n_suspicious,
                             "baseline": baseline, "m5_4": m5_4, "m5_4_1": m5_4_1})

        print(f"[{dataset}] n={baseline['n_scored']}  crop_suspicious ticks={n_suspicious}")
        print(f"  baseline (temporal off) : conf_acc={baseline['micro_confirmed_accuracy']}  "
              f"CW={baseline['confidently_wrong_confirmations']}")
        print(f"  m5_4     (no crop gate) : conf_acc={m5_4['micro_confirmed_accuracy']}  "
              f"CW={m5_4['confidently_wrong_confirmations']}  "
              f"corroborations={m5_4['temporal_corroborations']} "
              f"(correct={m5_4['temporal_corroborations_correct']}, wrong={m5_4['temporal_corroborations_wrong']})")
        print(f"  m5_4_1   (crop gate on) : conf_acc={m5_4_1['micro_confirmed_accuracy']}  "
              f"CW={m5_4_1['confidently_wrong_confirmations']}  "
              f"corroborations={m5_4_1['temporal_corroborations']} "
              f"(correct={m5_4_1['temporal_corroborations_correct']}, wrong={m5_4_1['temporal_corroborations_wrong']})")
        delta_vs_baseline = m5_4_1["confidently_wrong_confirmations"] - baseline["confidently_wrong_confirmations"]
        delta_vs_m54 = m5_4_1["confidently_wrong_confirmations"] - m5_4["confidently_wrong_confirmations"]
        flag = "  <-- STILL A REGRESSION vs baseline" if delta_vs_baseline > 0 else ""
        print(f"  m5_4_1 CW - baseline CW = {delta_vs_baseline}{flag}")
        print(f"  m5_4_1 CW - m5_4 CW     = {delta_vs_m54}")
        if m5_4["corroboration_examples"]:
            print("  m5_4 corroboration examples (no crop gate):")
            for ex in m5_4["corroboration_examples"]:
                mark = "OK" if ex["ocr_correct"] else "WRONG"
                print(f"    [{mark}] {ex['id']} {ex['field']}: predicted={ex['predicted']} gt={ex['ground_truth']} "
                      f"conf={ex['confidence']} crop_suspicious={ex['crop_suspicious']}")
        if m5_4_1["corroboration_examples"]:
            print("  m5_4_1 corroboration examples (crop gate on):")
            for ex in m5_4_1["corroboration_examples"]:
                mark = "OK" if ex["ocr_correct"] else "WRONG"
                print(f"    [{mark}] {ex['id']} {ex['field']}: predicted={ex['predicted']} gt={ex['ground_truth']} "
                      f"conf={ex['confidence']} crop_suspicious={ex['crop_suspicious']}")
        print()

    with open(os.path.join(OUT_DIR, "m5_4_1_crop_integrity_eval.json"), "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"Wrote {OUT_DIR}/m5_4_1_crop_integrity_eval.json")


if __name__ == "__main__":
    main()
