"""M4.1/M4.2 aggregation: turns m4_ocr_benchmark.py's raw per-sample JSON
into every table the M4 report needs. Read-only / analysis-only, isolated
from production. Usage:

    python -m app.eval.m4_aggregate_report [--raw PATH] [--out DIR]
"""

import argparse
import json
import os
import statistics
from typing import Dict, List, Optional

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")
SCALAR_FIELDS = ("hr", "spo2", "etco2", "temp", "rr")  # nibp handled separately (sys/dia)
ALL_FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "etco2", "temp", "rr")

DEFAULT_RAW = "app/eval/tier2_data/external_monitor_video/m4_ocr_report/m4_raw_results_start0.json"
DEFAULT_OUT = "app/eval/tier2_data/external_monitor_video/m4_ocr_report"

PREPROCESS_VARIANT_NAMES = [
    "CURRENT_BASELINE", "GRAYSCALE", "UPSCALE_2X", "UPSCALE_3X", "CONTRAST",
    "OTSU", "ADAPTIVE_THRESHOLD", "SHARPEN", "UPSCALE_2X_SHARPEN", "UPSCALE_2X_ADAPTIVE",
    "PSM6", "PSM7", "PSM10",
]


def _round_display(value: float, decimals: int) -> float:
    return round(value, decimals) if decimals else float(round(value))


def _gt_field_value(gt: dict, field: str) -> Optional[float]:
    """gt is the per-vital ground truth dict for one sample (e.g.
    {"nibp": "150/80/103", "temp": 98.6}). Splits nibp's sys/dia/mean."""
    if field in ("nibpSystolic", "nibpDiastolic", "nibpMean"):
        raw = gt.get("nibp")
        if raw is None:
            return None
        parts = raw.split("/")
        if field == "nibpSystolic":
            return float(parts[0])
        if field == "nibpDiastolic":
            return float(parts[1])
        return float(parts[2]) if len(parts) > 2 else None
    return gt.get(field)


FIELD_DECIMALS = {"hr": 0, "spo2": 0, "nibpSystolic": 0, "nibpDiastolic": 0, "etco2": 0, "temp": 1, "rr": 0}
FIELD_VITAL = {"hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr", "nibpSystolic": "nibp", "nibpDiastolic": "nibp"}


def _extract_predicted_field(variant_record: dict, field: str) -> Optional[float]:
    if variant_record is None:
        return None
    value = variant_record.get("value")
    if value is None:
        return None
    if field in ("nibpSystolic", "nibpDiastolic"):
        if not isinstance(value, dict):
            return None
        key = "systolic" if field == "nibpSystolic" else "diastolic"
        return value.get(key)
    if isinstance(value, dict):
        return None  # nibp value on a non-nibp field shouldn't happen
    return value


def _digit_accuracy(pred: Optional[float], gt: float, decimals: int) -> Optional[float]:
    """Fraction of matching digits, right-aligned on the integer part,
    when pred exists. 'Digit-level accuracy where useful' -- a coarse
    signal, not a claimed formal metric."""
    if pred is None:
        return 0.0
    ps = str(int(round(pred))) if not decimals else f"{pred:.{decimals}f}"
    gs = str(int(round(gt))) if not decimals else f"{gt:.{decimals}f}"
    ps = ps.replace(".", "")
    gs = gs.replace(".", "")
    length = max(len(ps), len(gs))
    ps = ps.rjust(length, "\0")
    gs = gs.rjust(length, "\0")
    matches = sum(1 for a, b in zip(ps, gs) if a == b)
    return matches / length


def build_category_table(results: List[dict]) -> Dict[str, Dict[str, int]]:
    table = {v: {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0} for v in VITALS}
    for r in results:
        for v in VITALS:
            cat = r["per_vital"][v]["category"]
            table[v][cat] += 1
    return table


def build_variant_metrics(results: List[dict]) -> Dict[str, Dict[str, dict]]:
    """variant -> field -> {n, correct, wrong, missing, errors[], confidences[], latencies[], calibration[(conf,correct)]}"""
    out = {
        variant: {field: {"n": 0, "correct": 0, "wrong": 0, "missing": 0, "abs_errors": [], "digit_acc": [], "confidences": [], "pre_lat": [], "tess_lat": [], "calibration": []} for field in ALL_FIELDS}
        for variant in PREPROCESS_VARIANT_NAMES
    }
    for r in results:
        for vital in VITALS:
            pv = r["per_vital"][vital]
            if pv["category"] != "D":
                continue
            gt_dict = pv["ground_truth"]  # the scalar/string GT value stored for this one vital
            fields = ["nibpSystolic", "nibpDiastolic"] if vital == "nibp" else [f for f, vv in FIELD_VITAL.items() if vv == vital]
            for field in fields:
                if vital == "nibp":
                    parts = str(gt_dict).split("/")
                    gt_val = float(parts[0]) if field == "nibpSystolic" else float(parts[1])
                else:
                    gt_val = float(gt_dict)
                gt_display = _round_display(gt_val, FIELD_DECIMALS[field])

                for variant in PREPROCESS_VARIANT_NAMES:
                    vrec = pv["variants"].get(variant)
                    stat = out[variant][field]
                    pred = _extract_predicted_field(vrec, field)
                    stat["n"] += 1
                    stat["pre_lat"].append(vrec.get("preprocess_latency_s", 0.0) if vrec else 0.0)
                    stat["tess_lat"].append(vrec.get("tesseract_latency_s", 0.0) if vrec else 0.0)
                    if pred is None:
                        stat["missing"] += 1
                        stat["calibration"].append((vrec.get("confidence", 0.0) if vrec else 0.0, False))
                        continue
                    pred_display = _round_display(pred, FIELD_DECIMALS[field])
                    correct = pred_display == gt_display
                    stat["confidences"].append(vrec.get("confidence", 0.0))
                    stat["calibration"].append((vrec.get("confidence", 0.0), correct))
                    stat["abs_errors"].append(abs(pred_display - gt_display))
                    stat["digit_acc"].append(_digit_accuracy(pred_display, gt_display, FIELD_DECIMALS[field]))
                    if correct:
                        stat["correct"] += 1
                    else:
                        stat["wrong"] += 1
    return out


def _lat_stats(values: List[float]) -> dict:
    if not values:
        return {"mean": None, "median": None, "p95": None}
    ordered = sorted(values)
    return {
        "mean": statistics.mean(values) * 1000,
        "median": statistics.median(values) * 1000,
        "p95": ordered[int(0.95 * (len(ordered) - 1))] * 1000,
    }


def summarize_variant_metrics(variant_metrics: Dict[str, Dict[str, dict]]) -> dict:
    out = {}
    for variant, fields in variant_metrics.items():
        field_summary = {}
        total_n = total_correct = total_wrong = total_missing = 0
        all_pre_lat, all_tess_lat = [], []
        for field, stat in fields.items():
            n, correct, wrong, missing = stat["n"], stat["correct"], stat["wrong"], stat["missing"]
            parsed = n - missing
            field_summary[field] = {
                "n": n,
                "exact_accuracy": (correct / n) if n else None,
                "parse_rate": (parsed / n) if n else None,
                "wrong_rate": (wrong / n) if n else None,
                "missing_rate": (missing / n) if n else None,
                "mean_confidence": statistics.mean(stat["confidences"]) if stat["confidences"] else None,
                "mean_digit_accuracy": statistics.mean(stat["digit_acc"]) if stat["digit_acc"] else None,
                "mean_abs_error": statistics.mean(stat["abs_errors"]) if stat["abs_errors"] else None,
            }
            total_n += n
            total_correct += correct
            total_wrong += wrong
            total_missing += missing
            all_pre_lat.extend(stat["pre_lat"])
            all_tess_lat.extend(stat["tess_lat"])
        out[variant] = {
            "per_field": field_summary,
            "overall": {
                "n": total_n,
                "exact_accuracy": (total_correct / total_n) if total_n else None,
                "parse_rate": ((total_n - total_missing) / total_n) if total_n else None,
                "wrong_rate": (total_wrong / total_n) if total_n else None,
                "missing_rate": (total_missing / total_n) if total_n else None,
            },
            "preprocess_latency_ms": _lat_stats(all_pre_lat),
            "tesseract_latency_ms": _lat_stats(all_tess_lat),
            "total_latency_ms": _lat_stats([a + b for a, b in zip(all_pre_lat, all_tess_lat)]),
        }
    return out


def calibration_table(variant_metrics: Dict[str, Dict[str, dict]], variant: str) -> List[dict]:
    buckets = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
    all_points = []
    for field, stat in variant_metrics[variant].items():
        all_points.extend(stat["calibration"])
    out = []
    for lo, hi in buckets:
        pts = [c for conf, c in all_points if lo <= conf < hi]
        out.append({"range": f"[{lo},{hi})", "n": len(pts), "accuracy": (sum(pts) / len(pts)) if pts else None})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=DEFAULT_RAW)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    with open(args.raw) as f:
        raw = json.load(f)
    results = raw["results"]

    categories = build_category_table(results)
    variant_metrics = build_variant_metrics(results)
    summary = summarize_variant_metrics(variant_metrics)
    calibration_baseline = calibration_table(variant_metrics, "CURRENT_BASELINE")

    out = {
        "n_samples": len(results),
        "categories": categories,
        "variant_summary": summary,
        "baseline_calibration": calibration_baseline,
        "elapsed_seconds": raw.get("elapsed_seconds"),
    }

    out_path = os.path.join(args.out, "m4_aggregate.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"Wrote {out_path}")

    # quick text render
    print("\n=== Category breakdown (A=cand-miss, B=fieldcnn-miss, C=selection-fail, D=reaches-ocr, E=no-GT) ===")
    for v in VITALS:
        c = categories[v]
        print(f"  {v:8s} A={c['A']:3d} B={c['B']:3d} C={c['C']:3d} D={c['D']:3d} E={c['E']:3d}")

    print("\n=== Overall exact accuracy per variant ===")
    for variant in PREPROCESS_VARIANT_NAMES:
        o = summary[variant]["overall"]
        acc = f"{o['exact_accuracy']*100:.1f}%" if o["exact_accuracy"] is not None else "n/a"
        print(f"  {variant:22s} n={o['n']:4d}  acc={acc:>7s}  parse={o['parse_rate']*100:.1f}%  missing={o['missing_rate']*100:.1f}%")


if __name__ == "__main__":
    main()
