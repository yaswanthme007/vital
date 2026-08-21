"""M4.2.1: PSM10_SELECTIVE -- a per-vital PSM routing configuration.

HR, SpO2, RR  -> PSM10 (baseline pixels, --psm 10)
EtCO2, Temp, NIBP -> CURRENT production PSM/config (--psm 8 / --psm 8 decimal / --psm 6)

CRITICAL: this does NOT re-run OCR, candidate generation, FieldCNN, or
selection. m4_ocr_benchmark.py's M4.1/M4.2 run already recorded BOTH the
CURRENT_BASELINE variant (production pixels + production per-vital PSM)
AND the PSM10 variant (production pixels + --psm 10) for every one of the
same 172 category-D crops. PSM10_SELECTIVE is built by reading, per field,
whichever of those two ALREADY-MEASURED records corresponds to the vital's
assigned PSM -- guaranteeing byte-for-byte identical crops/boxes/FieldCNN
predictions/selection decisions to M4.1/M4.2, and reusing real, previously
-measured OCR latency rather than re-timing anything. This is the most
rigorous way to satisfy "use EXACTLY the same 172 category-D selected
crops... do not rerun candidate generation/FieldCNN/selection."

Isolated eval tooling only -- reads m4_raw_results_start0.json (produced by
m4_ocr_benchmark.py), writes only under m4_ocr_report/. Never touches
production code, the 52 source images, annotations, or model artifacts.

Usage:
    python -m app.eval.m4_2_1_psm_selective
"""

import json
import os
import statistics
from typing import Dict, List, Optional

RAW_PATH = "app/eval/tier2_data/external_monitor_video/m4_ocr_report/m4_raw_results_start0.json"
OUT_DIR = "app/eval/tier2_data/external_monitor_video/m4_ocr_report"

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")
ALL_FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "etco2", "temp", "rr")
FIELD_VITAL = {"hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr", "nibpSystolic": "nibp", "nibpDiastolic": "nibp"}
FIELD_DECIMALS = {"hr": 0, "spo2": 0, "nibpSystolic": 0, "nibpDiastolic": 0, "etco2": 0, "temp": 1, "rr": 0}

# The routing table this experiment exists to test.
SELECTIVE_SOURCE = {
    "hr": "PSM10",
    "spo2": "PSM10",
    "rr": "PSM10",
    "etco2": "CURRENT_BASELINE",
    "temp": "CURRENT_BASELINE",
    "nibp": "CURRENT_BASELINE",
}

COMPARE_VARIANTS = ["CURRENT_BASELINE", "PSM7", "PSM10", "PSM10_SELECTIVE"]


def _round_display(value: float, decimals: int) -> float:
    return round(value, decimals) if decimals else float(round(value))


def _extract_predicted_field(variant_record: dict, field: str) -> Optional[float]:
    if variant_record is None:
        return None
    value = variant_record.get("value")
    if value is None:
        return None
    if field in ("nibpSystolic", "nibpDiastolic"):
        if not isinstance(value, dict):
            return None
        return value.get("systolic" if field == "nibpSystolic" else "diastolic")
    if isinstance(value, dict):
        return None
    return value


def build_field_stats(results: List[dict], variant_name: str) -> Dict[str, dict]:
    """variant_name: one of CURRENT_BASELINE/PSM7/PSM10 (read directly from
    stored records) or the synthetic 'PSM10_SELECTIVE' (routed per-field)."""
    stats = {f: {"n": 0, "correct": 0, "wrong": 0, "missing": 0, "confidences": [], "pre_lat": [], "tess_lat": []} for f in ALL_FIELDS}
    for r in results:
        for vital in VITALS:
            pv = r["per_vital"][vital]
            if pv["category"] != "D":
                continue
            gt_dict = pv["ground_truth"]
            fields = ["nibpSystolic", "nibpDiastolic"] if vital == "nibp" else [f for f, vv in FIELD_VITAL.items() if vv == vital]
            source = SELECTIVE_SOURCE[vital] if variant_name == "PSM10_SELECTIVE" else variant_name
            vrec = pv["variants"][source]
            for field in fields:
                if vital == "nibp":
                    parts = str(gt_dict).split("/")
                    gt_val = float(parts[0]) if field == "nibpSystolic" else float(parts[1])
                else:
                    gt_val = float(gt_dict)
                gt_display = _round_display(gt_val, FIELD_DECIMALS[field])

                stat = stats[field]
                stat["n"] += 1
                stat["pre_lat"].append(vrec.get("preprocess_latency_s", 0.0))
                stat["tess_lat"].append(vrec.get("tesseract_latency_s", 0.0))
                pred = _extract_predicted_field(vrec, field)
                if pred is None:
                    stat["missing"] += 1
                    continue
                pred_display = _round_display(pred, FIELD_DECIMALS[field])
                stat["confidences"].append(vrec.get("confidence", 0.0))
                if pred_display == gt_display:
                    stat["correct"] += 1
                else:
                    stat["wrong"] += 1
    return stats


def _lat_stats_ms(values: List[float]) -> dict:
    if not values:
        return {"mean": None, "median": None, "p95": None}
    ordered = sorted(values)
    return {
        "mean": statistics.mean(values) * 1000,
        "median": statistics.median(values) * 1000,
        "p95": ordered[int(0.95 * (len(ordered) - 1))] * 1000,
    }


def summarize(stats: Dict[str, dict]) -> dict:
    per_field = {}
    tot_n = tot_c = tot_w = tot_m = 0
    all_pre, all_tess = [], []
    field_accs = []
    for field, s in stats.items():
        n, c, w, m = s["n"], s["correct"], s["wrong"], s["missing"]
        acc = c / n if n else None
        per_field[field] = {
            "n": n,
            "exact_accuracy": acc,
            "parse_rate": (n - m) / n if n else None,
            "wrong_rate": w / n if n else None,
            "missing_rate": m / n if n else None,
            "mean_confidence": statistics.mean(s["confidences"]) if s["confidences"] else None,
            "latency_ms": _lat_stats_ms([a + b for a, b in zip(s["pre_lat"], s["tess_lat"])]),
        }
        tot_n += n
        tot_c += c
        tot_w += w
        tot_m += m
        all_pre.extend(s["pre_lat"])
        all_tess.extend(s["tess_lat"])
        if acc is not None:
            field_accs.append(acc)
    return {
        "per_field": per_field,
        "overall": {
            "n": tot_n,
            "correct": tot_c,
            "wrong": tot_w,
            "missing": tot_m,
            "micro_accuracy": tot_c / tot_n if tot_n else None,
            "macro_accuracy": statistics.mean(field_accs) if field_accs else None,
        },
        "latency_ms": _lat_stats_ms([a + b for a, b in zip(all_pre, all_tess)]),
    }


def main() -> None:
    with open(RAW_PATH) as f:
        raw = json.load(f)
    results = raw["results"]

    summaries = {}
    for variant in COMPARE_VARIANTS:
        stats = build_field_stats(results, variant)
        summaries[variant] = summarize(stats)

    out_path = os.path.join(OUT_DIR, "m4_2_1_psm_selective.json")
    with open(out_path, "w") as f:
        json.dump(summaries, f, indent=2, default=str)
    print(f"Wrote {out_path}\n")

    fields = list(ALL_FIELDS)
    print("=== Per-field exact accuracy ===")
    header = "field".ljust(16) + "".join(v.rjust(20) for v in COMPARE_VARIANTS)
    print(header)
    for f in fields:
        row = f.ljust(16)
        for v in COMPARE_VARIANTS:
            acc = summaries[v]["per_field"][f]["exact_accuracy"]
            row += (f"{acc*100:.1f}%" if acc is not None else "n/a").rjust(20)
        print(row)

    print()
    print("=== Overall ===")
    for v in COMPARE_VARIANTS:
        o = summaries[v]["overall"]
        lat = summaries[v]["latency_ms"]
        print(
            f"{v:20s} n={o['n']:4d} correct={o['correct']:4d} wrong={o['wrong']:4d} missing={o['missing']:4d} "
            f"micro_acc={o['micro_accuracy']*100:5.1f}% macro_acc={o['macro_accuracy']*100:5.1f}% "
            f"lat mean={lat['mean']:.1f}ms median={lat['median']:.1f}ms p95={lat['p95']:.1f}ms"
        )


if __name__ == "__main__":
    main()
