"""M5.4 Phase 6: real reconcile()-level validation of temporal corroboration
against the SAME real-data records M5.3 already produced and validated
(app/eval/tier2_data/m5_3_report/*.json, the m5_3_tracked arm -- the actual
production calibrated+tracked ROI path). This is deliberately NOT a new OCR
run: it takes the identical per-(frame, field) OCR reads M5.3 measured and
replays them through the REAL, imported app.validation.reconcile.reconcile()
twice -- once exactly as before (temporal_state=None) and once with M5.4's
new path enabled (temporal_state=initial_temporal_state()) -- so the ONLY
variable between the two runs is this milestone's own change.

"Confidently-wrong confirmation" uses the EXACT same definition
app.eval.m5_2_calibration_eval.run_reconcile_replay already established: a
tick where the raw OCR read was wrong AND the value reconcile() shows as
CONFIRMED this tick equals that wrong raw read (i.e. it was genuinely
accepted, not held) AND that confirmed value does not match ground truth.
Reused by definition, not reimplemented, so this number is directly
comparable to every prior milestone's own count.

Usage:
    python -m app.eval.m5_4_confirmation_eval
"""

import json
import os
from typing import Dict, List, Optional

from app.eval.m5_2_calibration_eval import FIELD_DECIMALS, FIELD_TO_VITAL, FIELDS, _round_display
from app.validation.reconcile import initial_confirmed_state, reconcile
from app.validation.rules import normalize_temp_celsius
from app.validation.temporal import initial_temporal_state

M53_DIR = "app/eval/tier2_data/m5_3_report"
OUT_DIR = "app/eval/tier2_data/m5_4_report"

DATASETS = {
    "frozen_A": os.path.join(M53_DIR, "m5_3_frozen_A.json"),
    "frozen_B[sample_0001]": os.path.join(M53_DIR, "m5_3_frozen_B_sample_0001.json"),
    "frozen_B[sample_0011]": os.path.join(M53_DIR, "m5_3_frozen_B_sample_0011.json"),
    "dense_B_anchors": os.path.join(M53_DIR, "m5_3_dense_B_anchors.json"),
}


def load_records(path: str, arm: str = "m5_3_tracked") -> List[dict]:
    with open(path) as f:
        data = json.load(f)
    return data["arms"][arm]["ocr_records"]


def replay(records: List[dict], dataset: str, use_temporal: bool, interval_ms: int = 1000,
           base_ts: int = 1_700_000_000_000) -> dict:
    by_id: Dict[str, Dict[str, dict]] = {}
    for r in records:
        by_id.setdefault(r["id"], {})[r["field"]] = r
    sample_ids = sorted(by_id.keys())

    confirmed = initial_confirmed_state(base_ts)
    temporal_state = initial_temporal_state() if use_temporal else None

    field_stats = {f: {"n": 0, "ocr_correct": 0, "confirmed_correct": 0, "wrong_but_confirmed": 0,
                        "temporal_corroborations": 0, "temporal_corroborations_correct": 0} for f in FIELDS}
    corroboration_examples: List[dict] = []

    for i, sid in enumerate(sample_ids):
        ts = base_ts + i * interval_ms
        field_records = by_id[sid]

        raw_reading: dict = {"timestamp": ts}
        per_vital_confidence: Dict[str, float] = {}
        for field in FIELDS:
            rec = field_records.get(field)
            raw_reading[field] = rec["predicted"] if rec else None
            if rec is not None:
                per_vital_confidence[FIELD_TO_VITAL[field]] = rec["confidence"]

        reading, confirmed, flagged = reconcile(raw_reading, per_vital_confidence, confirmed, temporal_state=temporal_state)

        for field in FIELDS:
            rec = field_records.get(field)
            if rec is None or rec["excluded_from_scoring"]:
                continue
            gt = rec["ground_truth"]
            if gt is None:
                continue
            dec = FIELD_DECIMALS[field]
            gt_disp = _round_display(normalize_temp_celsius(gt) if field == "temp" else gt, dec) if field == "temp" else gt
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
            # A grouped (NIBP) frameNote joins per-field notes with "; " --
            # split it so a corroboration on e.g. nibpDiastolic is not missed
            # just because nibpSystolic's note happens to lead the string.
            for part in f.get("frameNote", "").split("; "):
                if "temporal corroboration" not in part:  # rendered TEXT has a space, not the reason key's underscore
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
                        "dataset": dataset, "id": sid, "field": field,
                        "predicted": rec["predicted"] if rec else None,
                        "ground_truth": rec["ground_truth"] if rec else None,
                        "confidence": rec["confidence"] if rec else None,
                        "ocr_correct": correct,
                    })

    total_n = sum(v["n"] for v in field_stats.values())
    total_ocr_correct = sum(v["ocr_correct"] for v in field_stats.values())
    total_confirmed_correct = sum(v["confirmed_correct"] for v in field_stats.values())
    total_wrong_but_confirmed = sum(v["wrong_but_confirmed"] for v in field_stats.values())
    total_corroborations = sum(v["temporal_corroborations"] for v in field_stats.values())
    total_corroborations_correct = sum(v["temporal_corroborations_correct"] for v in field_stats.values())

    return {
        "dataset": dataset, "use_temporal": use_temporal, "n_scored": total_n,
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
    print("=== M5.4 Phase 6: reconcile()-level before/after, real M5.3 tracked-arm records ===\n")
    for dataset, path in DATASETS.items():
        records = load_records(path)
        baseline = replay(records, dataset, use_temporal=False)
        enabled = replay(records, dataset, use_temporal=True)
        all_results.append({"dataset": dataset, "baseline": baseline, "temporal_enabled": enabled})

        print(f"[{dataset}] n={baseline['n_scored']}")
        print(f"  baseline         : confirmed_acc={baseline['micro_confirmed_accuracy']}  "
              f"confidently_wrong={baseline['confidently_wrong_confirmations']}")
        print(f"  temporal_enabled : confirmed_acc={enabled['micro_confirmed_accuracy']}  "
              f"confidently_wrong={enabled['confidently_wrong_confirmations']}  "
              f"new_corroborations={enabled['temporal_corroborations']} "
              f"(correct={enabled['temporal_corroborations_correct']}, wrong={enabled['temporal_corroborations_wrong']})")
        delta_cw = enabled["confidently_wrong_confirmations"] - baseline["confidently_wrong_confirmations"]
        flag = "  <-- REGRESSION" if delta_cw > 0 else ""
        print(f"  delta confidently_wrong = {delta_cw}{flag}")
        if enabled["corroboration_examples"]:
            print(f"  corroboration examples:")
            for ex in enabled["corroboration_examples"]:
                mark = "OK" if ex["ocr_correct"] else "WRONG"
                print(f"    [{mark}] {ex['id']} {ex['field']}: predicted={ex['predicted']} gt={ex['ground_truth']} conf={ex['confidence']}")
        print()

    with open(os.path.join(OUT_DIR, "m5_4_confirmation_eval.json"), "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"Wrote {OUT_DIR}/m5_4_confirmation_eval.json")


if __name__ == "__main__":
    main()
