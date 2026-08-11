import argparse
import glob
import json
import os
import statistics
import time
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

from app.pipeline.ocr import OcrEngine
from app.pipeline.read_frame import get_default_engine, read_frame

# etco2's ground truth keeps 1-decimal precision in the label, but the
# simulator only ever RENDERS it as a rounded integer on screen (see
# simulator/render/common.py format_value) — temp is the only vital drawn
# with a decimal. Comparing OCR against the raw label value would penalize
# etco2 for a display-rounding gap no OCR engine could ever close, so every
# field is compared at the precision it was actually rendered at.
FIELD_DISPLAY_DECIMALS = {
    "hr": 0,
    "spo2": 0,
    "nibpSystolic": 0,
    "nibpDiastolic": 0,
    "nibpMean": 0,
    "etco2": 0,
    "temp": 1,
    "rr": 0,
}

FIELD_TO_VITAL_GROUP = {
    "hr": "hr",
    "spo2": "spo2",
    "etco2": "etco2",
    "temp": "temp",
    "rr": "rr",
    "nibpSystolic": "nibp",
    "nibpDiastolic": "nibp",
    "nibpMean": "nibp",
}

CONFIDENCE_BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]


def _round_display(value: float, decimals: int) -> float:
    return round(value, decimals) if decimals else float(round(value))


def load_dataset(dataset_dir: str) -> List[dict]:
    """A dataset dir is simulator.generate's --count output: sample_*.png +
    sample_*.json pairs plus manifest.json. Returns a list of
    {"id", "png_path", "label"} sorted by sample id."""
    json_paths = sorted(
        p for p in glob.glob(os.path.join(dataset_dir, "sample_*.json"))
    )
    samples = []
    for json_path in json_paths:
        with open(json_path) as f:
            label = json.load(f)
        png_path = json_path[: -len(".json")] + ".png"
        if not os.path.isfile(png_path):
            continue
        samples.append({"id": label.get("id", os.path.basename(json_path)), "png_path": png_path, "label": label})
    return samples


def evaluate_sample(png_path: str, label: dict, engine: OcrEngine) -> dict:
    img = np.array(Image.open(png_path).convert("RGB"))
    reading, confidences = read_frame(img, engine=engine)

    gt_values = label.get("values", {})
    fields = {}
    for field, decimals in FIELD_DISPLAY_DECIMALS.items():
        gt_raw = gt_values.get(field)
        if gt_raw is None:
            continue
        gt_display = _round_display(gt_raw, decimals)

        pred = reading.get(field)
        pred_display = _round_display(pred, decimals) if pred is not None else None
        correct = pred_display is not None and pred_display == gt_display
        abs_error = abs(pred_display - gt_display) if pred_display is not None else None

        fields[field] = {
            "ground_truth": gt_display,
            "predicted": pred_display,
            "correct": correct,
            "abs_error": abs_error,
            "confidence": confidences.get(FIELD_TO_VITAL_GROUP[field], 0.0),
        }

    augmentations = [e["type"] for e in label.get("augmentations", [])]
    return {
        "id": label.get("id"),
        "layout": label.get("layout"),
        "is_augmented": len(augmentations) > 0,
        "augmentations": augmentations,
        "fields": fields,
    }


def _bucket_accuracy(results: List[dict], predicate) -> dict:
    n = correct = 0
    for r in results:
        if not predicate(r):
            continue
        for info in r["fields"].values():
            n += 1
            correct += 1 if info["correct"] else 0
    return {"n": n, "accuracy": (correct / n) if n else None}


def aggregate(results: List[dict]) -> dict:
    per_vital: Dict[str, dict] = {}
    for field in FIELD_DISPLAY_DECIMALS:
        n = correct = 0
        errors: List[float] = []
        for r in results:
            info = r["fields"].get(field)
            if info is None:
                continue
            n += 1
            correct += 1 if info["correct"] else 0
            if info["abs_error"] is not None:
                errors.append(info["abs_error"])
        per_vital[field] = {
            "n": n,
            "accuracy": (correct / n) if n else None,
            "mae": (sum(errors) / len(errors)) if errors else None,
        }

    total_n = sum(v["n"] for v in per_vital.values())
    total_correct = sum(
        1
        for r in results
        for info in r["fields"].values()
        if info["correct"]
    )
    overall_accuracy = (total_correct / total_n) if total_n else None

    clean_vs_augmented = {
        "clean": _bucket_accuracy(results, lambda r: not r["is_augmented"]),
        "augmented": _bucket_accuracy(results, lambda r: r["is_augmented"]),
    }

    layouts = sorted({r["layout"] for r in results if r["layout"] is not None})
    by_layout = {layout: _bucket_accuracy(results, lambda r, layout=layout: r["layout"] == layout) for layout in layouts}

    calibration = []
    for lo, hi in CONFIDENCE_BUCKETS:
        n = correct = 0
        for r in results:
            for info in r["fields"].values():
                if lo <= info["confidence"] < hi:
                    n += 1
                    correct += 1 if info["correct"] else 0
        calibration.append({"range": f"[{lo}, {hi})", "n": n, "accuracy": (correct / n) if n else None})

    return {
        "sample_count": len(results),
        "total_fields_evaluated": total_n,
        "overall_accuracy": overall_accuracy,
        "per_vital": per_vital,
        "clean_vs_augmented": clean_vs_augmented,
        "by_layout": by_layout,
        "confidence_calibration": calibration,
    }


def _fmt_pct(value: Optional[float]) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _fmt_mae(value: Optional[float]) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


def render_text_summary(summary: dict) -> str:
    lines = []
    lines.append(f"Samples evaluated: {summary['sample_count']}  (fields: {summary['total_fields_evaluated']})")
    lines.append(f"Overall field accuracy: {_fmt_pct(summary['overall_accuracy'])}")
    lines.append("")
    lines.append("Per-vital accuracy / MAE:")
    for field, stats in summary["per_vital"].items():
        lines.append(f"  {field:16s} n={stats['n']:4d}  acc={_fmt_pct(stats['accuracy']):>7s}  mae={_fmt_mae(stats['mae'])}")
    lines.append("")
    lines.append("Clean vs augmented:")
    for bucket, stats in summary["clean_vs_augmented"].items():
        lines.append(f"  {bucket:10s} n={stats['n']:4d}  acc={_fmt_pct(stats['accuracy'])}")
    lines.append("")
    lines.append("By layout:")
    for layout, stats in summary["by_layout"].items():
        lines.append(f"  {layout:10s} n={stats['n']:4d}  acc={_fmt_pct(stats['accuracy'])}")
    lines.append("")
    lines.append("Confidence calibration (does low confidence predict wrong reads?):")
    for bucket in summary["confidence_calibration"]:
        lines.append(f"  conf {bucket['range']:>10s}  n={bucket['n']:4d}  acc={_fmt_pct(bucket['accuracy'])}")
    return "\n".join(lines)


def run_eval(dataset_dir: str, engine: Optional[OcrEngine] = None) -> dict:
    engine = engine or get_default_engine()
    samples = load_dataset(dataset_dir)
    if not samples:
        raise ValueError(f"No sample_*.json/.png pairs found under {dataset_dir}")

    started = time.time()
    results = [evaluate_sample(s["png_path"], s["label"], engine) for s in samples]
    elapsed = time.time() - started

    summary = aggregate(results)
    summary["dataset_dir"] = dataset_dir
    summary["elapsed_seconds"] = round(elapsed, 2)
    return {"summary": summary, "results": results}


# ─── S11: head-to-head engine comparison ───────────────────────────────


def run_head_to_head(dataset_dir: str, engines: Dict[str, OcrEngine]) -> dict:
    """Runs the SAME evaluation as run_eval(), once per named engine, over
    the SAME dataset_dir -- the judge-facing comparison S11 asks for.
    `engines` is e.g. {"tesseract": TesseractEngine(), "onnx": OnnxDigitEngine()}.
    """
    per_engine = {}
    for name, engine in engines.items():
        report = run_eval(dataset_dir, engine=engine)
        per_engine[name] = report["summary"]
    return {"dataset_dir": dataset_dir, "engines": per_engine}


def _row(label: str, names: List[str], values_by_name: Dict[str, Optional[float]], width: int = 16) -> str:
    return "  " + label.ljust(width) + "".join(_fmt_pct(values_by_name.get(name)).rjust(14) for name in names)


def render_head_to_head_summary(head_to_head: dict) -> str:
    engines = head_to_head["engines"]
    names = list(engines.keys())
    lines = [f"Head-to-head: {' vs '.join(names)}", f"Dataset: {head_to_head['dataset_dir']}", ""]

    lines.append("Overall accuracy:")
    lines.append(_row("", names, {n: engines[n]["overall_accuracy"] for n in names}, width=2))
    lines.append("")

    lines.append("Per-vital accuracy:")
    lines.append("  " + "field".ljust(16) + "".join(n.rjust(14) for n in names))
    fields = list(next(iter(engines.values()))["per_vital"].keys())
    for field in fields:
        values = {n: engines[n]["per_vital"].get(field, {}).get("accuracy") for n in names}
        lines.append(_row(field, names, values))
    lines.append("")

    lines.append("Clean vs augmented accuracy:")
    lines.append("  " + "bucket".ljust(16) + "".join(n.rjust(14) for n in names))
    for bucket in ("clean", "augmented"):
        values = {n: engines[n]["clean_vs_augmented"].get(bucket, {}).get("accuracy") for n in names}
        lines.append(_row(bucket, names, values))
    lines.append("")

    lines.append("Confidence calibration (does low confidence predict wrong reads, for EACH engine):")
    lines.append("  " + "range".ljust(16) + "".join(n.rjust(14) for n in names))
    calibration_by_name = {n: {b["range"]: b for b in engines[n]["confidence_calibration"]} for n in names}
    ranges = [b["range"] for b in next(iter(engines.values()))["confidence_calibration"]]
    for r in ranges:
        values = {n: calibration_by_name[n].get(r, {}).get("accuracy") for n in names}
        lines.append(_row(r, names, values))

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate OCR accuracy over a simulator-generated dataset.")
    parser.add_argument("--dataset", required=True, help="Dataset dir (simulator generate.py --count output)")
    parser.add_argument("--out", default=None, help="Path to write the JSON report (default: <dataset>/eval_report.json)")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Run Tesseract AND the ONNX engine (S11) over the same dataset and print a head-to-head report",
    )
    args = parser.parse_args()

    if args.compare:
        from app.pipeline.ocr import TesseractEngine

        engines: Dict[str, OcrEngine] = {"tesseract": TesseractEngine()}
        try:
            from app.pipeline.onnx_engine import OnnxDigitEngine, model_available

            if model_available():
                engines["onnx"] = OnnxDigitEngine()
            else:
                print("ONNX model not found under models/ -- comparing Tesseract only.")
                print("Train it with: python -m simulator.train.train_cnn")
        except ImportError:
            print("onnxruntime not installed -- comparing Tesseract only.")

        head_to_head = run_head_to_head(args.dataset, engines)
        summary_text = render_head_to_head_summary(head_to_head)

        out_path = args.out or os.path.join(args.dataset, "head_to_head_report.json")
        with open(out_path, "w") as f:
            json.dump(head_to_head, f, indent=2)
        summary_path = os.path.splitext(out_path)[0] + ".txt"
        with open(summary_path, "w") as f:
            f.write(summary_text + "\n")

        print(summary_text)
        print()
        print(f"Wrote {out_path}")
        print(f"Wrote {summary_path}")
        return

    report = run_eval(args.dataset)

    out_path = args.out or os.path.join(args.dataset, "eval_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    summary_text = render_text_summary(report["summary"])
    summary_path = os.path.splitext(out_path)[0] + ".txt"
    with open(summary_path, "w") as f:
        f.write(summary_text + "\n")

    print(summary_text)
    print()
    print(f"Wrote {out_path}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
