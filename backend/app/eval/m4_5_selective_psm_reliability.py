"""M4.5: does the NARROWER selective-PSM routing M4.3 §12/§14 identified as
evidence-supported (SpO2 + RR -> --psm 10, HR left alone) safely compose
with M4.4's rules-layer fixes (HR range, temp unit normalization, NIBP/EtCO2
confidence)?

Two configurations, both running through the real, current (M4.4-fixed)
production code:

  A) M4.4_BASELINE  -- app.pipeline.ocr.TesseractEngine, completely
     unmodified. This already includes every M4.4 fix (HR range widened,
     temp normalization wired into reconcile(), NIBP/EtCO2 confidence
     fixes) since those live in production code, not in a variant switch.

  B) M4_5_SELECTIVE -- NarrowSelectivePsmEngine below, a subclass that
     exists ONLY in this eval file. It routes ONLY spo2/rr to --psm 10;
     hr/temp/etco2/nibp all delegate to TesseractEngine.read_vital()
     unchanged. This is DELIBERATELY narrower than M4.3's own
     SelectivePsmEngine (app.eval.m4_3_reliability), which also routed hr
     -> psm10 -- M4.3 §12/§14 found that hurt HR's confirmed-state
     reliability, so M4.5 does not repeat it. app/pipeline/ocr.py is never
     modified by this file.

Reuses M4.3's own run_variant()/replay_reconcile() and M4.3's own
field_summary()/_category_d_mask() (app.eval.m4_3_reliability /
app.eval.m4_3_analysis), imported and called, not reimplemented. Writes
ONLY under app/eval/tier2_data/external_monitor_video/m4_5_report/ --
m4_3_report/ and m4_4_report/ are never opened for writing.

Usage:
    python -m app.eval.m4_5_selective_psm_reliability
"""

import json
import os

from app.eval.harness import load_dataset
from app.eval.m4_3_analysis import FIELDS as ANALYSIS_FIELDS
from app.eval.m4_3_analysis import _category_d_mask, field_summary
from app.eval.m4_3_reliability import DATASET_DIR, GT_PATH, replay_reconcile, run_variant
from app.pipeline.ocr import TesseractEngine, _DIGIT_CONFIG, _DECIMAL_CONFIG, _NIBP_CONFIG, _ETCO2_CONFIG
from app.validation.rules import normalize_temp_celsius

OUT_DIR = os.path.join(DATASET_DIR, "m4_5_report")
M4_1_RAW_PATH = os.path.join(DATASET_DIR, "m4_ocr_report", "m4_raw_results_start0.json")

_PSM10_DIGIT_CONFIG = "--psm 10 -c tessedit_char_whitelist=0123456789"
_NARROW_PSM10_VITALS = {"spo2", "rr"}  # HR explicitly excluded -- see module docstring


class NarrowSelectivePsmEngine(TesseractEngine):
    """Eval-only. Routes spo2/rr to --psm 10; hr/temp/etco2/nibp unchanged.
    Never imported by app.pipeline.*."""

    def read_vital(self, crop, vital_type):
        if vital_type in _NARROW_PSM10_VITALS:
            return self._read_scalar(crop, _PSM10_DIGIT_CONFIG, decimal=False)
        return super().read_vital(crop, vital_type)


def _print_current_config() -> None:
    print("=== Current production OCR config (verified from app.pipeline.ocr, not assumed) ===")
    print(f"  _DIGIT_CONFIG (hr/spo2/rr baseline) = {_DIGIT_CONFIG!r}")
    print(f"  _DECIMAL_CONFIG (temp)              = {_DECIMAL_CONFIG!r}")
    print(f"  _NIBP_CONFIG (M4.4)                 = {_NIBP_CONFIG!r}")
    print(f"  _ETCO2_CONFIG (M4.4)                = {_ETCO2_CONFIG!r}")
    print(f"  M4.5 selective psm10 config          = {_PSM10_DIGIT_CONFIG!r}")
    print(f"  M4.5 routes psm10 to: {sorted(_NARROW_PSM10_VITALS)} (hr excluded)")
    from app.validation.rules import RANGE_BOUNDS, FAHRENHEIT_BOUNDS
    print(f"  RANGE_BOUNDS['hr']  = {RANGE_BOUNDS['hr']} (M4.4: was (20,250))")
    print(f"  RANGE_BOUNDS['temp']= {RANGE_BOUNDS['temp']}, FAHRENHEIT_BOUNDS = {FAHRENHEIT_BOUNDS}")
    print(f"  normalize_temp_celsius(98.6) = {normalize_temp_celsius(98.6)} (expect 37.0)")


_VITAL_OF_FIELD = {"hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr", "nibpSystolic": "nibp", "nibpDiastolic": "nibp"}


def _field_category_map() -> dict:
    """sample_id -> field -> M4.1's own stored category letter (A/B/C/D/E),
    read directly from m4_raw_results_start0.json -- never re-derived, since
    candidate-generation/FieldCNN/selection are identical for every PSM
    variant (only the OCR step within category D differs)."""
    with open(M4_1_RAW_PATH) as f:
        raw = json.load(f)["results"]
    out = {}
    for r in raw:
        out[r["id"]] = {}
        for field, vital in _VITAL_OF_FIELD.items():
            pv = r["per_vital"].get(vital, {})
            out[r["id"]][field] = pv.get("category")
    return out


def build_taxonomy(timeline: list, category_map: dict) -> dict:
    """Preserves M4.3's A/B/C/D/E taxonomy, extended per this milestone's
    task: A=candidate-gen miss, B=classifier miss, C=selection failure (all
    3 read from M4.1's own stored categorization, unmodified). The
    category-D (reaches_ocr) population is further split by what happened
    AFTER OCR, which M4.1's taxonomy never tracked: D_ocr_value_failure =
    OCR itself was wrong/missing; E_rules_layer_rejected = OCR was CORRECT
    but reconcile() (range/jump/confidence) held it anyway; confirmed_correct
    = OCR correct AND it reached the confirmed state. Category "E" in M4.1's
    own scheme (ground_truth_unavailable) and any tick lacking GT both fold
    into not_evaluable here -- distinct concepts, same "can't be scored"
    bucket, kept together to avoid a same-letter collision with this
    milestone's own new "E" meaning."""
    counts = {f: {"A_candidate_gen_miss": 0, "B_classifier_miss": 0, "C_selection_failure": 0, "D_ocr_value_failure": 0, "E_rules_layer_rejected": 0, "confirmed_correct": 0, "not_evaluable": 0} for f in ANALYSIS_FIELDS}
    for fr in timeline:
        for field in ANALYSIS_FIELDS:
            cat = category_map.get(fr["id"], {}).get(field)
            e = fr["fields"][field]
            if e["gt"] is None or cat is None or cat == "E":
                counts[field]["not_evaluable"] += 1
                continue
            if cat == "A":
                counts[field]["A_candidate_gen_miss"] += 1
            elif cat == "B":
                counts[field]["B_classifier_miss"] += 1
            elif cat == "C":
                counts[field]["C_selection_failure"] += 1
            elif cat == "D":
                if e["ocr_class"] in ("wrong", "missing"):
                    counts[field]["D_ocr_value_failure"] += 1
                else:
                    # M4.4 report §4: temp's confirmed_correct as computed
                    # by the reused field_summary()/timeline is always
                    # False, because it compares the (correctly) Celsius
                    # confirmed value against GT still stored in the
                    # originally-displayed Fahrenheit scale -- a units
                    # mismatch in the SCORING code, not production. Score
                    # temp here the same unit-corrected way the M4.4 report
                    # already established, so this taxonomy doesn't repeat
                    # that same misleading "always rejected" bucketing.
                    if field == "temp":
                        gt_c = round(normalize_temp_celsius(e["gt"]), 1)
                        confirmed_ok = e["confirmed_value"] is not None and round(e["confirmed_value"], 1) == gt_c
                    else:
                        confirmed_ok = bool(e["confirmed_correct"])
                    if confirmed_ok:
                        counts[field]["confirmed_correct"] += 1
                    else:
                        counts[field]["E_rules_layer_rejected"] += 1
    return counts


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    _print_current_config()

    with open(GT_PATH) as f:
        gt_all = json.load(f)["values"]
    samples = load_dataset(DATASET_DIR)
    print(f"\nLoaded {len(samples)} samples")

    baseline_engine = TesseractEngine()
    selective_engine = NarrowSelectivePsmEngine()

    print("\n=== Running M4.4_BASELINE (current production, unmodified) over all frames ===")
    baseline_records = run_variant(samples, gt_all, "M4.4_BASELINE", baseline_engine)

    print("\n=== Running M4.5_SELECTIVE (spo2/rr -> psm10, hr untouched) over all frames ===")
    selective_records = run_variant(samples, gt_all, "M4.5_SELECTIVE", selective_engine)

    with open(os.path.join(OUT_DIR, "m4_5_raw_records.json"), "w") as f:
        json.dump({"baseline": baseline_records, "selective": selective_records}, f, indent=2, default=str)
    print(f"\nWrote {os.path.join(OUT_DIR, 'm4_5_raw_records.json')}")

    for tag, records in (("baseline", baseline_records), ("selective", selective_records)):
        timeline = replay_reconcile(records, interval_ms=1000)
        with open(os.path.join(OUT_DIR, f"m4_5_timeline_{tag}_interval1000.json"), "w") as f:
            json.dump(timeline, f, indent=2, default=str)
        print(f"Wrote timeline for {tag}")

    d_mask = _category_d_mask()
    category_map = _field_category_map()
    summaries = {}
    taxonomies = {}
    for tag in ("baseline", "selective"):
        with open(os.path.join(OUT_DIR, f"m4_5_timeline_{tag}_interval1000.json")) as f:
            timeline = json.load(f)
        summaries[tag] = {field: field_summary(timeline, field, d_mask) for field in ANALYSIS_FIELDS}
        taxonomies[tag] = build_taxonomy(timeline, category_map)

    with open(os.path.join(OUT_DIR, "m4_5_analysis_summary.json"), "w") as f:
        json.dump(summaries, f, indent=2, default=str)
    print(f"Wrote {os.path.join(OUT_DIR, 'm4_5_analysis_summary.json')}")

    with open(os.path.join(OUT_DIR, "m4_5_taxonomy.json"), "w") as f:
        json.dump(taxonomies, f, indent=2, default=str)
    print(f"Wrote {os.path.join(OUT_DIR, 'm4_5_taxonomy.json')}")

    print("\n=== Failure taxonomy (A/B/C/D/E) ===")
    for tag in ("baseline", "selective"):
        print(f"-- {tag} --")
        for field in ANALYSIS_FIELDS:
            print(f"  {field:16s}{taxonomies[tag][field]}")

    print("\n=== M4.4_BASELINE vs M4.5_SELECTIVE: OCR accuracy / confirmed accuracy ===")
    header = f"{'field':16s}{'ocr_B':>8s}{'ocr_S':>8s}{'conf_B':>8s}{'conf_S':>8s}{'wrong_B':>9s}{'wrong_S':>9s}"
    print(header)
    for field in ANALYSIS_FIELDS:
        b, s = summaries["baseline"][field], summaries["selective"][field]

        def p(x):
            return f"{x*100:.1f}%" if x is not None else "n/a"

        print(f"{field:16s}{p(b['ocr_accuracy']):>8s}{p(s['ocr_accuracy']):>8s}{p(b['confirmed_accuracy']):>8s}{p(s['confirmed_accuracy']):>8s}{b['confirmed_wrong']:>9d}{s['confirmed_wrong']:>9d}")

    # Temp: unit-corrected confirmed accuracy, both variants (see M4.4 report §4).
    print("\n=== Temp: unit-corrected confirmed accuracy (GT converted via the same normalize_temp_celsius) ===")
    for tag in ("baseline", "selective"):
        with open(os.path.join(OUT_DIR, f"m4_5_timeline_{tag}_interval1000.json")) as f:
            timeline = json.load(f)
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
        print(f"  {tag}: {correct}/{n} = {correct/n*100:.1f}%")


if __name__ == "__main__":
    main()
