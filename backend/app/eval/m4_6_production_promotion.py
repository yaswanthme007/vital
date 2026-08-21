"""M4.6: does the REAL, PROMOTED app.pipeline.ocr.TesseractEngine (spo2/rr
now dispatching to --psm 10 in production code itself) reproduce M4.5's
eval-only NarrowSelectivePsmEngine numbers, running over the same 52-frame
dataset through the same reused M4.3 harness (run_variant/replay_reconcile)
and M4.3 analysis (field_summary/_category_d_mask)?

This is a REPRODUCTION check, not a new evaluation: M4.5 already established
the evidence. This script exists to prove the production code path (no
eval-only subclass involved) produces the identical result, byte for byte,
before this milestone declares GO.

Usage:
    python -m app.eval.m4_6_production_promotion
"""

import json
import os

from app.eval.harness import load_dataset
from app.eval.m4_3_analysis import FIELDS as ANALYSIS_FIELDS
from app.eval.m4_3_analysis import _category_d_mask, field_summary
from app.eval.m4_3_reliability import DATASET_DIR, GT_PATH, replay_reconcile, run_variant
from app.pipeline.ocr import (
    TesseractEngine,
    _DECIMAL_CONFIG,
    _DIGIT_CONFIG,
    _DIGIT_PSM10_CONFIG,
    _ETCO2_CONFIG,
    _NIBP_CONFIG,
    _PSM10_VITALS,
)
from app.validation.rules import normalize_temp_celsius

OUT_DIR = os.path.join(DATASET_DIR, "m4_6_report")
M4_5_REPORT_DIR = os.path.join(DATASET_DIR, "m4_5_report")


def _print_current_config() -> None:
    print("=== PROMOTED production OCR config (verified from app.pipeline.ocr) ===")
    print(f"  _DIGIT_CONFIG (hr baseline)         = {_DIGIT_CONFIG!r}")
    print(f"  _DIGIT_PSM10_CONFIG (spo2/rr, NEW)  = {_DIGIT_PSM10_CONFIG!r}")
    print(f"  _PSM10_VITALS routed to psm10       = {sorted(_PSM10_VITALS)} (hr excluded)")
    print(f"  _DECIMAL_CONFIG (temp)              = {_DECIMAL_CONFIG!r}")
    print(f"  _NIBP_CONFIG (M4.4, unchanged)      = {_NIBP_CONFIG!r}")
    print(f"  _ETCO2_CONFIG (M4.4, unchanged)     = {_ETCO2_CONFIG!r}")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    _print_current_config()
    assert _PSM10_VITALS == {"spo2", "rr"}, "production routing set no longer matches M4.5 evidence"

    with open(GT_PATH) as f:
        gt_all = json.load(f)["values"]
    samples = load_dataset(DATASET_DIR)
    print(f"\nLoaded {len(samples)} samples")

    production_engine = TesseractEngine()

    print("\n=== Running M4.6_PRODUCTION (real promoted TesseractEngine, no eval subclass) over all frames ===")
    production_records = run_variant(samples, gt_all, "M4.6_PRODUCTION", production_engine)

    with open(os.path.join(OUT_DIR, "m4_6_raw_records.json"), "w") as f:
        json.dump({"production": production_records}, f, indent=2, default=str)
    print(f"\nWrote {os.path.join(OUT_DIR, 'm4_6_raw_records.json')}")

    timeline = replay_reconcile(production_records, interval_ms=1000)
    with open(os.path.join(OUT_DIR, "m4_6_timeline_production_interval1000.json"), "w") as f:
        json.dump(timeline, f, indent=2, default=str)
    print("Wrote timeline for production")

    d_mask = _category_d_mask()
    summary = {field: field_summary(timeline, field, d_mask) for field in ANALYSIS_FIELDS}
    with open(os.path.join(OUT_DIR, "m4_6_analysis_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Wrote {os.path.join(OUT_DIR, 'm4_6_analysis_summary.json')}")

    # Temp unit-corrected accuracy, same methodology as M4.4/M4.5.
    n = correct = 0
    for fr in timeline:
        t = fr["fields"]["temp"]
        if t["gt"] is None:
            continue
        n += 1
        gt_c = round(normalize_temp_celsius(t["gt"]), 1)
        conf = t["confirmed_value"]
        if conf is not None and round(conf, 1) == gt_c:
            correct += 1
    temp_unit_corrected = correct / n if n else None
    print(f"\nTemp unit-corrected confirmed accuracy: {correct}/{n} = {temp_unit_corrected*100:.1f}%")

    # --- Reproduction check against M4.5's stored selective numbers ---
    with open(os.path.join(M4_5_REPORT_DIR, "m4_5_analysis_summary.json")) as f:
        m4_5_summary = json.load(f)["selective"]

    print("\n=== M4.5_SELECTIVE (eval-only engine) vs M4.6_PRODUCTION (real engine) reproduction check ===")
    header = f"{'field':16s}{'ocr_M4.5':>10s}{'ocr_M4.6':>10s}{'conf_M4.5':>11s}{'conf_M4.6':>11s}{'wrong_M4.5':>12s}{'wrong_M4.6':>12s}{'match':>7s}"
    print(header)
    mismatches = []
    for field in ANALYSIS_FIELDS:
        m5, m6 = m4_5_summary[field], summary[field]

        def p(x):
            return f"{x*100:.1f}%" if x is not None else "n/a"

        is_match = (
            m5["ocr_accuracy"] == m6["ocr_accuracy"]
            and m5["confirmed_accuracy"] == m6["confirmed_accuracy"]
            and m5["confirmed_wrong"] == m6["confirmed_wrong"]
        )
        if not is_match:
            mismatches.append(field)
        print(
            f"{field:16s}{p(m5['ocr_accuracy']):>10s}{p(m6['ocr_accuracy']):>10s}"
            f"{p(m5['confirmed_accuracy']):>11s}{p(m6['confirmed_accuracy']):>11s}"
            f"{m5['confirmed_wrong']:>12d}{m6['confirmed_wrong']:>12d}"
            f"{'YES' if is_match else 'NO':>7s}"
        )

    with open(os.path.join(OUT_DIR, "m4_6_reproduction_check.json"), "w") as f:
        json.dump({"mismatched_fields": mismatches, "m4_5_selective": m4_5_summary, "m4_6_production": summary}, f, indent=2, default=str)

    if mismatches:
        print(f"\n!!! MISMATCH on fields: {mismatches} -- investigate before declaring GO !!!")
    else:
        print("\nAll fields match M4.5's selective evaluation exactly. Production reproduces the tested evidence.")


if __name__ == "__main__":
    main()
