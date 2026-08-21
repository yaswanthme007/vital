"""M4.3 Test C/D/F/G aggregation: turns the raw per-frame timelines produced
by app.eval.m4_3_reliability into the summary tables the M4.3 report cites.
Pure aggregation over already-computed, already-real data -- no new OCR, no
new reconcile() calls, nothing fabricated. Writes only under
app/eval/tier2_data/external_monitor_video/m4_3_report/.

Usage:
    python -m app.eval.m4_3_analysis
"""

import json
import os
import statistics
from typing import Dict, List, Optional

DATASET_DIR = "app/eval/tier2_data/external_monitor_video"
OUT_DIR = os.path.join(DATASET_DIR, "m4_3_report")
M4_RAW_PATH = os.path.join(DATASET_DIR, "m4_ocr_report", "m4_raw_results_start0.json")

FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "etco2", "temp", "rr")
FIELD_TO_VITAL = {"hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr", "nibpSystolic": "nibp", "nibpDiastolic": "nibp"}
CHANGED_FIELDS = ("hr", "spo2", "rr")  # routed to PSM10 under SELECTIVE
UNCHANGED_FIELDS = ("nibpSystolic", "nibpDiastolic", "etco2", "temp")


def _load_timeline(tag: str, variant: str) -> List[dict]:
    with open(os.path.join(OUT_DIR, f"m4_3_timeline_{variant}_{tag}.json")) as f:
        return json.load(f)


def _category_d_mask() -> Dict[str, Dict[str, bool]]:
    """sample_id -> field -> True if M4.1/M4.2's stored run classified this
    (sample, vital) as category D (reaches_ocr). Used only to reproduce
    M4.1/M4.2.1's exact accuracy denominator for a cross-check; the primary
    M4.3 numbers use the full live re-run (all categories), not this mask."""
    with open(M4_RAW_PATH) as f:
        raw = json.load(f)["results"]
    mask: Dict[str, Dict[str, bool]] = {}
    for r in raw:
        sid = r["id"]
        mask[sid] = {}
        for vital, pv in r["per_vital"].items():
            is_d = pv.get("category") == "D"
            for field, v in FIELD_TO_VITAL.items():
                if v == vital:
                    mask[sid][field] = is_d
    return mask


def _mean(xs: List[float]) -> Optional[float]:
    return statistics.mean(xs) if xs else None


def field_summary(timeline: List[dict], field: str, d_mask: Optional[Dict[str, Dict[str, bool]]] = None) -> dict:
    entries = [(fr["id"], fr["fields"][field]) for fr in timeline]
    scored = [(sid, e) for sid, e in entries if e["gt"] is not None]
    n_scored = len(scored)

    ocr_correct = [e for _, e in scored if e["ocr_class"] == "correct"]
    ocr_wrong = [e for _, e in scored if e["ocr_class"] == "wrong"]
    ocr_missing = [e for _, e in scored if e["ocr_class"] == "missing"]

    confirmed_correct = [e for _, e in scored if e["confirmed_correct"] is True]
    confirmed_wrong = [e for _, e in scored if e["confirmed_correct"] is False]

    # Test C: what happened to WRONG ocr ticks once they hit reconcile()?
    wrong_accepted = [e for e in ocr_wrong if e["accepted"]]  # became the confirmed value THIS tick
    wrong_rejected = [e for e in ocr_wrong if not e["accepted"]]
    wrong_reject_reasons: Dict[str, int] = {}
    for e in wrong_rejected:
        wrong_reject_reasons[e["reason"]] = wrong_reject_reasons.get(e["reason"], 0) + 1

    # The other half of the story Test C doesn't ask for by name but that
    # this dataset makes unavoidable: CORRECT ocr ticks that reconcile()
    # nonetheless rejects (range/jump/confidence-gate false negatives). A
    # field can be 100% OCR-correct and 0% confirmed-correct if every one of
    # those correct reads gets rejected before ever reaching the confirmed
    # state -- exactly what happened to temp and nibp in this dataset (see
    # report). Tracked here so that collapse is visible in the numbers, not
    # just inferred from confirmed_accuracy alone.
    correct_accepted = [e for e in ocr_correct if e["accepted"]]
    correct_rejected = [e for e in ocr_correct if not e["accepted"]]
    correct_reject_reasons: Dict[str, int] = {}
    for e in correct_rejected:
        correct_reject_reasons[e["reason"]] = correct_reject_reasons.get(e["reason"], 0) + 1
    # of the wrong ticks that WERE accepted, how many actually left the
    # confirmed state wrong (should be == len(wrong_accepted); sanity re-derived)
    wrong_accepted_and_confirmed_wrong = [e for e in wrong_accepted if e["confirmed_correct"] is False]

    # Test D: what happened to MISSING ocr ticks?
    missing_confirmed_correct = [e for e in ocr_missing if e["confirmed_correct"] is True]
    missing_confirmed_wrong = [e for e in ocr_missing if e["confirmed_correct"] is False]

    # Confidence reaching the gate, by outcome class
    conf_by_class = {
        "correct": _mean([e["fused_confidence"] for e in ocr_correct]),
        "wrong": _mean([e["fused_confidence"] for e in ocr_wrong]),
        "missing": _mean([e["fused_confidence"] for e in ocr_missing]),
    }
    # classifier vs ocr: for wrong ticks, which signal was the binding (lower) one?
    wrong_binding_classifier = sum(1 for e in ocr_wrong if e["classifier_confidence"] <= e["ocr_confidence"])
    wrong_binding_ocr = sum(1 for e in ocr_wrong if e["ocr_confidence"] < e["classifier_confidence"])
    wrong_high_classifier_conf = sum(1 for e in ocr_wrong if e["classifier_confidence"] >= 90)

    # Confidence bucket calibration (production's own tiers: <70 ai_low, 70-89 ai_medium, >=90 ai_high)
    def _bucket(c: float) -> str:
        if c >= 90:
            return "ai_high (>=90)"
        if c >= 70:
            return "ai_medium (70-89)"
        return "ai_low (<70)"

    calibration: Dict[str, Dict[str, int]] = {}
    for _, e in scored:
        b = _bucket(e["fused_confidence"])
        calibration.setdefault(b, {"correct": 0, "wrong": 0, "missing": 0})
        calibration[b][e["ocr_class"]] += 1

    # Test G: temporal stability, over the SCORED subsequence only (unscored
    # ticks excluded -- no GT to judge them, see report Limitations).
    seq = [e["confirmed_correct"] for _, e in scored]
    runs = []
    if seq:
        cur_val, cur_len = seq[0], 1
        for v in seq[1:]:
            if v == cur_val:
                cur_len += 1
            else:
                runs.append((cur_val, cur_len))
                cur_val, cur_len = v, 1
        runs.append((cur_val, cur_len))
    wrong_runs = [length for val, length in runs if val is False]
    correct_runs = [length for val, length in runs if val is True]
    single_frame_spikes = sum(1 for length in wrong_runs if length == 1)
    two_frame_spikes = sum(1 for length in wrong_runs if length == 2)
    persistent_wrong_runs = sum(1 for length in wrong_runs if length >= 3)
    longest_wrong_run = max(wrong_runs) if wrong_runs else 0
    longest_correct_run = max(correct_runs) if correct_runs else 0
    # "corrected later": number of wrong runs that are NOT the last run in
    # the sequence (i.e. did get followed by a correct run at some point
    # within this 52-frame recording) vs those still wrong at the last
    # scored frame.
    wrong_run_indices = [i for i, (val, _) in enumerate(runs) if val is False]
    corrected_later = sum(1 for i in wrong_run_indices if i != len(runs) - 1)
    still_wrong_at_end = sum(1 for i in wrong_run_indices if i == len(runs) - 1)

    # raw OCR value-change count (full chronological sequence, both readings non-missing)
    raw_vals = [e["raw_ocr"] for _, e in entries]
    value_changes = sum(1 for a, b in zip(raw_vals, raw_vals[1:]) if a is not None and b is not None and a != b)
    confirmed_vals = [e["confirmed_value"] for _, e in entries]
    confirmed_value_changes = sum(1 for a, b in zip(confirmed_vals, confirmed_vals[1:]) if a is not None and b is not None and a != b)

    out = {
        "n_total_frames": len(entries),
        "n_scored": n_scored,
        "ocr_correct": len(ocr_correct),
        "ocr_wrong": len(ocr_wrong),
        "ocr_missing": len(ocr_missing),
        "ocr_accuracy": len(ocr_correct) / n_scored if n_scored else None,
        "ocr_wrong_rate": len(ocr_wrong) / n_scored if n_scored else None,
        "ocr_missing_rate": len(ocr_missing) / n_scored if n_scored else None,
        "confirmed_correct": len(confirmed_correct),
        "confirmed_wrong": len(confirmed_wrong),
        "confirmed_accuracy": len(confirmed_correct) / n_scored if n_scored else None,
        "wrong_ocr_accepted_into_confirmed": len(wrong_accepted),
        "wrong_ocr_rejected_held": len(wrong_rejected),
        "wrong_ocr_reject_reasons": wrong_reject_reasons,
        "wrong_accepted_and_confirmed_wrong_sanity": len(wrong_accepted_and_confirmed_wrong),
        "correct_ocr_accepted": len(correct_accepted),
        "correct_ocr_rejected": len(correct_rejected),
        "correct_ocr_reject_reasons": correct_reject_reasons,
        "missing_confirmed_correct": len(missing_confirmed_correct),
        "missing_confirmed_wrong": len(missing_confirmed_wrong),
        "mean_fused_confidence_by_ocr_class": conf_by_class,
        "wrong_ticks_binding_signal_classifier": wrong_binding_classifier,
        "wrong_ticks_binding_signal_ocr": wrong_binding_ocr,
        "wrong_ticks_with_classifier_confidence_ge90": wrong_high_classifier_conf,
        "confidence_calibration": calibration,
        "temporal": {
            "runs": runs,
            "longest_wrong_run_scored_ticks": longest_wrong_run,
            "longest_correct_run_scored_ticks": longest_correct_run,
            "single_frame_wrong_spikes": single_frame_spikes,
            "two_frame_wrong_spikes": two_frame_spikes,
            "persistent_wrong_runs_ge3": persistent_wrong_runs,
            "wrong_episodes_corrected_within_recording": corrected_later,
            "wrong_episodes_still_wrong_at_last_frame": still_wrong_at_end,
            "raw_ocr_value_changes_full_sequence": value_changes,
            "confirmed_value_changes_full_sequence": confirmed_value_changes,
        },
    }

    if d_mask is not None:
        d_scored = [(sid, e) for sid, e in scored if d_mask.get(sid, {}).get(field)]
        n_d = len(d_scored)
        d_correct = sum(1 for _, e in d_scored if e["ocr_class"] == "correct")
        out["category_d_only_crosscheck"] = {
            "n": n_d,
            "ocr_accuracy": d_correct / n_d if n_d else None,
        }
    return out


def main() -> None:
    d_mask = _category_d_mask()
    summary = {}
    for tag in ("interval1000", "interval5000"):
        summary[tag] = {}
        for variant in ("baseline", "selective"):
            timeline = _load_timeline(tag, variant)
            summary[tag][variant] = {field: field_summary(timeline, field, d_mask) for field in FIELDS}

    out_path = os.path.join(OUT_DIR, "m4_3_analysis_summary.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Wrote {out_path}")

    # Console table: the headline comparison, interval=1000ms (production default)
    tag = "interval1000"
    print(f"\n=== {tag}: OCR accuracy vs confirmed accuracy, BASELINE vs SELECTIVE ===")
    header = f"{'field':16s}{'ocr_acc(B)':>12s}{'ocr_acc(S)':>12s}{'conf_acc(B)':>13s}{'conf_acc(S)':>13s}{'confirmed_wrong(B)':>20s}{'confirmed_wrong(S)':>20s}"
    print(header)
    for field in FIELDS:
        b = summary[tag]["baseline"][field]
        s = summary[tag]["selective"][field]

        def pct(x):
            return f"{x*100:.1f}%" if x is not None else "n/a"

        print(
            f"{field:16s}{pct(b['ocr_accuracy']):>12s}{pct(s['ocr_accuracy']):>12s}"
            f"{pct(b['confirmed_accuracy']):>13s}{pct(s['confirmed_accuracy']):>13s}"
            f"{b['confirmed_wrong']:>20d}{s['confirmed_wrong']:>20d}"
        )

    print(f"\n=== Category-D-only cross-check (should match M4.2.1 §2/§3 closely) ===")
    for field in FIELDS:
        b = summary[tag]["baseline"][field]["category_d_only_crosscheck"]
        s = summary[tag]["selective"][field]["category_d_only_crosscheck"]

        def pct(x):
            return f"{x*100:.1f}%" if x is not None else "n/a"

        print(f"{field:16s} n_d={b['n']:3d}  baseline_D_acc={pct(b['ocr_accuracy']):>7s}  selective_D_acc={pct(s['ocr_accuracy']):>7s}")


if __name__ == "__main__":
    main()
