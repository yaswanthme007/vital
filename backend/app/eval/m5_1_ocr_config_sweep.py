"""M5.1 Phase 1/2/3/4: reproduces the Tesseract confidence-collapse behaviour
tied to `tessedit_char_whitelist` and measures the effect of removing it, in
a controlled, single-variable experiment.

WHAT THIS ISOLATES. Two OCR engines are run on the IDENTICAL oracle
(ground-truth-box) crop, for every (sample, vital) pair in both datasets:

  PRODUCTION     -- app.pipeline.ocr.TesseractEngine, byte-for-byte
                    unmodified (imported, never reimplemented).
  NO_WHITELIST   -- NoWhitelistEngine below, a TesseractEngine subclass that
                    overrides read_vital() ONLY for hr/spo2/rr/temp (the
                    vitals whose config strings still carry
                    `tessedit_char_whitelist`, per ocr.py's own M4.4 comment
                    trail) and strips the whitelist clause via
                    _strip_whitelist() -- a regex over the PRODUCTION config
                    string itself, not a hand-typed duplicate, so the PSM
                    value and every other flag can never drift out of sync
                    with production. nibp/etco2 delegate to
                    TesseractEngine.read_vital() unchanged: M4.4 already
                    removed their whitelist, so there is nothing to isolate
                    there and this keeps this eval-only class byte-identical
                    to production on those two vitals (same pattern
                    m4_3_reliability.py's SelectivePsmEngine uses for ITS
                    out-of-scope vitals).

Preprocessing (_preprocess -- upscale/Otsu/pad), crop source (the same
warped ground-truth box, cropped once and handed to both engines), and every
other pipeline stage are held fixed. Whitelist presence/absence is the only
variable that changes between the two runs of a given (sample, vital).

DATASETS.
  A = app/eval/tier2_data/external_monitor_video (52 frames, GT boxes in
      each sample_*.json's "rois", GT values in m4_ocr_report/
      m4_ground_truth_values.json -- M4.1's manual transcription).
  B = app/eval/tier2_data/external_monitor_B (17 frames, GT boxes+values in
      m5_ground_truth_values.json -- M5's manual transcription). NIBP never
      appears in this recording (always dashes/"Manual" -- see that file's
      own vital_label_map) and Dataset B's Temp ground truth is a clipped
      box reading outside RANGE_BOUNDS["temp"] regardless (EVIDENCE.md sec
      9) -- Temp is recorded here (raw OCR/confidence) but excluded from
      Dataset B's correctness scoring, never silently dropped from output.

ORACLE CROP = the annotated ground-truth box, warped through
detect_screen()'s homography exactly as m4_ocr_benchmark.py's
_gt_box_for_vital does, cropped with ZERO margin (the tightest, most
literal "ground truth" box available) -- not the FieldCNN/candidate-
generator's selected box. Localization (candidate generation, FieldCNN,
ROI selection) is deliberately out of scope for M5.1; this script never
touches app.pipeline.tier2_roi or app.pipeline.field_classifier.

PHASE 5 (reconcile validation) lives in this same file's
`run_reconcile_replay()`: the per-sample OCR (value, confidence) records
computed above are fed, in dataset order, through the REAL, imported
app.validation.reconcile.reconcile() -- never reimplemented -- exactly as
app.eval.m4_3_reliability.replay_reconcile() already does for the Tier-2
pipeline. This is the only way to answer "does restoring confidence actually
change what reconcile() confirms" rather than inferring it from raw OCR
accuracy alone.

Nothing here trains a model, tunes a threshold, or modifies a dataset.

Usage:
    python -m app.eval.m5_1_ocr_config_sweep
"""

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from app.eval.harness import load_dataset
from app.eval.tier2_common import Box, warp_box
from app.pipeline.detect import detect_screen
from app.pipeline.ocr import (
    NibpValue,
    OcrEngine,
    TesseractEngine,
    _DECIMAL_CONFIG,
    _DIGIT_CONFIG,
    _DIGIT_PSM10_CONFIG,
    _PSM10_VITALS,
    _locate_tesseract_binary,
)
from app.pipeline.tier2_roi import _clip_box
from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile
from app.validation.rules import CONFIDENCE_MEDIUM_MIN, confidence_tier, is_in_range, is_jump_rejected, normalize_temp_celsius

import pytesseract

resolved = _locate_tesseract_binary(None)
if resolved:
    pytesseract.pytesseract.tesseract_cmd = resolved

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")
FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr")
FIELD_TO_VITAL: Dict[str, str] = {
    "hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr",
    "nibpSystolic": "nibp", "nibpDiastolic": "nibp", "nibpMean": "nibp",
}
FIELD_DECIMALS = {"hr": 0, "spo2": 0, "nibpSystolic": 0, "nibpDiastolic": 0, "nibpMean": 0, "etco2": 0, "temp": 1, "rr": 0}

DATASET_A_DIR = "app/eval/tier2_data/external_monitor_video"
DATASET_A_GT = os.path.join(DATASET_A_DIR, "m4_ocr_report", "m4_ground_truth_values.json")
DATASET_B_DIR = "app/eval/tier2_data/external_monitor_B"
DATASET_B_GT = os.path.join(DATASET_B_DIR, "m5_ground_truth_values.json")

OUT_DIR = "app/eval/tier2_data/m5_1_report"

# Dataset B's Temp ground truth is a clipped box reading a room-temperature
# value outside RANGE_BOUNDS["temp"] regardless of OCR (EVIDENCE.md sec 9) --
# scored separately, never folded into Dataset B's headline accuracy.
DATASET_B_EXCLUDE_FROM_SCORING = {"temp"}


# ─── The experiment's only independent variable ─────────────────────────


def _strip_whitelist(config: str) -> str:
    """Removes ` -c tessedit_char_whitelist=...` from a production config
    string, leaving the PSM and everything else byte-identical. Derived
    from the production constant itself (not hand-duplicated) so the two
    variants can never silently drift apart on anything but the whitelist."""
    return re.sub(r"\s*-c\s+tessedit_char_whitelist=\S+", "", config).strip()


_NO_WL_DIGIT_CONFIG = _strip_whitelist(_DIGIT_CONFIG)
_NO_WL_DECIMAL_CONFIG = _strip_whitelist(_DECIMAL_CONFIG)
_NO_WL_PSM10_CONFIG = _strip_whitelist(_DIGIT_PSM10_CONFIG)

assert _NO_WL_DIGIT_CONFIG == "--psm 8", _NO_WL_DIGIT_CONFIG
assert _NO_WL_DECIMAL_CONFIG == "--psm 8", _NO_WL_DECIMAL_CONFIG
assert _NO_WL_PSM10_CONFIG == "--psm 10", _NO_WL_PSM10_CONFIG


class NoWhitelistEngine(TesseractEngine):
    """Eval-only. See module docstring. Never imported by app.pipeline.*."""

    def read_vital(self, crop: np.ndarray, vital_type: str):
        if vital_type in ("nibp", "etco2"):
            return super().read_vital(crop, vital_type)  # M4.4 already whitelist-free; byte-identical to production
        if vital_type == "temp":
            return self._read_scalar(crop, _NO_WL_DECIMAL_CONFIG, decimal=True)
        if vital_type in _PSM10_VITALS:
            return self._read_scalar(crop, _NO_WL_PSM10_CONFIG, decimal=False)
        return self._read_scalar(crop, _NO_WL_DIGIT_CONFIG, decimal=False)


CONFIGS: Dict[str, OcrEngine] = {
    "production": TesseractEngine(),
    "no_whitelist": NoWhitelistEngine(),
}


# ─── Oracle crop extraction ──────────────────────────────────────────────


def _gt_box_for_vital(label: dict, vital: str, screen) -> Optional[Box]:
    raw = label.get("rois", {}).get(vital)
    if not raw or raw[2] <= 0 or raw[3] <= 0:
        return None
    box: Box = tuple(raw)
    if screen.detected and screen.homography is not None:
        box = warp_box(box, screen.homography)
    return box


def _oracle_crop(work_img: np.ndarray, box: Box) -> Optional[np.ndarray]:
    h, w = work_img.shape[:2]
    clipped = _clip_box(box, w, h)
    if clipped is None:
        return None
    x, y, bw, bh = clipped
    crop = work_img[y : y + bh, x : x + bw]
    return crop if crop.size else None


def _gt_for_sample(gt_all: dict, sample_id: str) -> Dict[str, Optional[float]]:
    """Splits a manually-transcribed 'nibp': 'sys/dia/mean' string into the
    3 numeric fields; every other field is already numeric. Identical
    convention to m4_3_reliability._gt_for_sample / m5_second_monitor_
    generalization._gt_for_sample."""
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


def _round_display(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, decimals) if decimals else float(round(value))


# ─── Phase 1/2/3/4: per-crop OCR sweep ───────────────────────────────────


def run_sweep(dataset_name: str, dataset_dir: str, gt_path: str, exclude_from_scoring: set) -> List[dict]:
    with open(gt_path) as f:
        gt_doc = json.load(f)
    gt_all = gt_doc["values"]

    samples = load_dataset(dataset_dir)
    print(f"[{dataset_name}] loaded {len(samples)} samples")

    records: List[dict] = []
    for s in samples:
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        screen = detect_screen(img)
        work_img = screen.image
        gt_values = _gt_for_sample(gt_all, s["id"])

        for vital in VITALS:
            box = _gt_box_for_vital(s["label"], vital, screen)
            if box is None:
                continue
            crop = _oracle_crop(work_img, box)
            if crop is None:
                continue

            group_fields = ["nibpSystolic", "nibpDiastolic", "nibpMean"] if vital == "nibp" else [vital]
            if all(gt_values[f] is None for f in group_fields):
                continue  # no GT value at all for this vital this frame -- nothing to score or learn from

            for config_name, engine in CONFIGS.items():
                t0 = time.perf_counter()
                value, confidence = engine.read_vital(crop, vital)
                latency_s = time.perf_counter() - t0

                if vital == "nibp":
                    assert isinstance(value, NibpValue)
                    parsed = {"nibpSystolic": value.systolic, "nibpDiastolic": value.diastolic, "nibpMean": value.mean}
                else:
                    parsed = {vital: value}

                for field in group_fields:
                    gt = gt_values[field]
                    if gt is None:
                        continue
                    decimals = FIELD_DECIMALS[field]
                    gt_disp = _round_display(gt, decimals)
                    pred = parsed[field]
                    pred_disp = _round_display(pred, decimals)
                    missing = pred is None
                    correct = (not missing) and pred_disp == gt_disp
                    excluded = field in exclude_from_scoring or FIELD_TO_VITAL[field] in exclude_from_scoring
                    records.append({
                        "dataset": dataset_name,
                        "config": config_name,
                        "id": s["id"],
                        "vital": vital,
                        "field": field,
                        "ground_truth": gt_disp,
                        "predicted": pred_disp,
                        "missing": missing,
                        "correct": None if excluded else correct,
                        "excluded_from_scoring": excluded,
                        "confidence": confidence,
                        "latency_s": latency_s,
                    })
    return records


# ─── Aggregation ──────────────────────────────────────────────────────────


def _clears_gate(confidence: float) -> bool:
    return confidence >= CONFIDENCE_MEDIUM_MIN


def aggregate(records: List[dict]) -> dict:
    by_dataset_config: Dict[Tuple[str, str], List[dict]] = {}
    for r in records:
        by_dataset_config.setdefault((r["dataset"], r["config"]), []).append(r)

    summary: Dict[str, dict] = {}
    for (dataset, config), recs in sorted(by_dataset_config.items()):
        scored = [r for r in recs if not r["excluded_from_scoring"]]
        n = len(scored)
        correct = [r for r in scored if r["correct"]]
        missing = [r for r in scored if r["missing"]]
        wrong = [r for r in scored if (not r["missing"]) and (not r["correct"])]

        correct_conf = [r["confidence"] for r in correct]
        mean_conf_on_correct = (sum(correct_conf) / len(correct_conf)) if correct_conf else None
        pct_correct_clearing_gate = (
            sum(1 for c in correct_conf if _clears_gate(c)) / len(correct_conf) if correct_conf else None
        )

        per_vital: Dict[str, dict] = {}
        for vital_name in VITALS:
            v_scored = [r for r in scored if r["vital"] == vital_name]
            v_correct = [r for r in v_scored if r["correct"]]
            v_correct_conf = [r["confidence"] for r in v_correct]
            per_vital[vital_name] = {
                "n": len(v_scored),
                "accuracy": (len(v_correct) / len(v_scored)) if v_scored else None,
                "missing": sum(1 for r in v_scored if r["missing"]),
                "mean_confidence_on_correct": (sum(v_correct_conf) / len(v_correct_conf)) if v_correct_conf else None,
                "pct_correct_clearing_gate": (
                    sum(1 for c in v_correct_conf if _clears_gate(c)) / len(v_correct_conf) if v_correct_conf else None
                ),
            }

        latencies = [r["latency_s"] for r in recs]
        key = f"{dataset}/{config}"
        summary[key] = {
            "dataset": dataset,
            "config": config,
            "n_scored": n,
            "accuracy": (len(correct) / n) if n else None,
            "missing_rate": (len(missing) / n) if n else None,
            "wrong_rate": (len(wrong) / n) if n else None,
            "mean_confidence_on_correct": mean_conf_on_correct,
            "pct_correct_clearing_confidence_gate": pct_correct_clearing_gate,
            "per_vital": per_vital,
            "mean_latency_ms": (sum(latencies) / len(latencies)) * 1000 if latencies else None,
            "n_excluded_from_scoring": sum(1 for r in recs if r["excluded_from_scoring"]),
        }
    return summary


# ─── Phase 5: reconcile() validation ─────────────────────────────────────


def run_reconcile_replay(records: List[dict], dataset: str, config: str, interval_ms: int = 1000, base_ts: int = 1_700_000_000_000) -> dict:
    """Feeds one dataset+config's OCR records through the REAL, imported
    reconcile() in dataset (chronological) order -- exactly the pattern
    app.eval.m4_3_reliability.replay_reconcile() already established for the
    Tier-2 pipeline, adapted to this script's per-field record shape.
    NIBP's 3 sub-fields share the group's single crop/confidence, matching
    read_frame()'s own per-vital (not per-field) confidence contract."""
    subset = [r for r in records if r["dataset"] == dataset and r["config"] == config]
    by_id: Dict[str, Dict[str, dict]] = {}
    for r in subset:
        by_id.setdefault(r["id"], {})[r["field"]] = r

    sample_ids = sorted(by_id.keys())
    confirmed = initial_confirmed_state(base_ts)
    timeline = []
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

        prior_snapshot = {f: confirmed[f].value for f in FIELDS}
        reading, confirmed, flagged = reconcile(raw_reading, per_vital_confidence, confirmed)

        frame_entry = {"id": sid, "index": i, "fields": {}}
        for field in FIELDS:
            rec = field_records.get(field)
            if rec is None or rec["excluded_from_scoring"]:
                continue
            gt = rec["ground_truth"]
            if gt is None:
                continue
            # reconcile() normalizes temp to Celsius BEFORE validating/storing
            # (rules.normalize_temp_celsius) -- the confirmed value it returns
            # is therefore Celsius-scale even though this dataset's raw GT/OCR
            # are transcribed at the Fahrenheit scale the monitor displays.
            # Apply the identical conversion to GT before comparing, or every
            # temp row would spuriously show confirmed_correct=False.
            gt_for_compare = normalize_temp_celsius(gt) if field == "temp" and gt is not None else gt
            gt_for_compare = _round_display(gt_for_compare, FIELD_DECIMALS[field]) if field == "temp" else gt_for_compare
            confirmed_disp = _round_display(reading[field], FIELD_DECIMALS[field])
            raw_ocr_for_compare = (
                _round_display(normalize_temp_celsius(rec["predicted"]), FIELD_DECIMALS[field])
                if field == "temp" and rec["predicted"] is not None
                else rec["predicted"]
            )

            raw_value = raw_reading.get(field)
            if field == "temp" and raw_value is not None:
                raw_value = normalize_temp_celsius(raw_value)  # mirrors reconcile()'s own pre-validation step
            reason = None
            if raw_value is None:
                reason = "unreadable"
            elif not is_in_range(field, raw_value):
                reason = "implausible_range"
            else:
                prior_value = prior_snapshot[field]
                elapsed = interval_ms / 1000.0 if i > 0 else None
                if is_jump_rejected(field, raw_value, prior_value, elapsed):
                    reason = "jump_rejected"
                else:
                    tier = confidence_tier(per_vital_confidence.get(FIELD_TO_VITAL[field], 0.0))
                    if tier == "ai_low":
                        reason = "low_confidence"
                    elif tier == "ai_medium":
                        reason = "medium_confidence"

            frame_entry["fields"][field] = {
                "gt": gt_for_compare,
                "raw_ocr": rec["predicted"],
                "raw_ocr_for_compare": raw_ocr_for_compare,
                "ocr_correct": rec["correct"],
                "confidence": rec["confidence"],
                "reason": reason,
                "confirmed_value": confirmed_disp,
                "confirmed_correct": confirmed_disp == gt_for_compare,
            }
        timeline.append(frame_entry)

    # Aggregate: confirmed accuracy + reject-reason-shaped breakdown.
    field_stats: Dict[str, dict] = {
        f: {"n": 0, "ocr_correct": 0, "confirmed_correct": 0, "ocr_correct_but_rejected": 0, "wrong_but_confirmed": 0, "reject_reasons": {}}
        for f in FIELDS
    }
    for frame in timeline:
        for field, entry in frame["fields"].items():
            st = field_stats[field]
            st["n"] += 1
            if entry["ocr_correct"]:
                st["ocr_correct"] += 1
            if entry["confirmed_correct"]:
                st["confirmed_correct"] += 1
            if entry["ocr_correct"] and not entry["confirmed_correct"]:
                st["ocr_correct_but_rejected"] += 1
            if (not entry["ocr_correct"]) and entry["confirmed_correct"] is False and entry["confirmed_value"] == entry["raw_ocr_for_compare"] and entry["raw_ocr_for_compare"] is not None:
                # confirmed value equals a WRONG raw OCR read -> a confidently-wrong confirmation
                st["wrong_but_confirmed"] += 1
            if entry["reason"] is not None:
                st["reject_reasons"][entry["reason"]] = st["reject_reasons"].get(entry["reason"], 0) + 1

    total_n = sum(v["n"] for v in field_stats.values())
    total_ocr_correct = sum(v["ocr_correct"] for v in field_stats.values())
    total_confirmed_correct = sum(v["confirmed_correct"] for v in field_stats.values())
    total_wrong_but_confirmed = sum(v["wrong_but_confirmed"] for v in field_stats.values())

    return {
        "dataset": dataset,
        "config": config,
        "n_frames": len(timeline),
        "field_stats": field_stats,
        "micro_ocr_accuracy": (total_ocr_correct / total_n) if total_n else None,
        "micro_confirmed_accuracy": (total_confirmed_correct / total_n) if total_n else None,
        "confidently_wrong_confirmations": total_wrong_but_confirmed,
        "timeline": timeline,
    }


# ─── Report rendering ─────────────────────────────────────────────────────


def _fmt_pct(v):
    return f"{v * 100:.1f}%" if v is not None else "n/a"


def _fmt_num(v, nd=1):
    return f"{v:.{nd}f}" if v is not None else "n/a"


def render_text_summary(sweep_summary: dict, reconcile_summaries: List[dict]) -> str:
    lines = ["=== M5.1 OCR config sweep (oracle crops, production vs no_whitelist) ===", ""]
    for key, s in sweep_summary.items():
        lines.append(f"{key}: n={s['n_scored']}  acc={_fmt_pct(s['accuracy'])}  "
                      f"missing={_fmt_pct(s['missing_rate'])}  "
                      f"mean_conf_on_correct={_fmt_num(s['mean_confidence_on_correct'])}  "
                      f"%correct_clearing_gate={_fmt_pct(s['pct_correct_clearing_confidence_gate'])}  "
                      f"latency={_fmt_num(s['mean_latency_ms'])}ms")
        for vital, v in s["per_vital"].items():
            if v["n"] == 0:
                continue
            lines.append(f"    {vital:8s} n={v['n']:3d}  acc={_fmt_pct(v['accuracy']):>7s}  "
                          f"mean_conf_on_correct={_fmt_num(v['mean_confidence_on_correct']):>6s}  "
                          f"%clearing_gate={_fmt_pct(v['pct_correct_clearing_gate'])}")
        lines.append("")

    lines.append("=== Phase 5: reconcile() validation ===")
    for rs in reconcile_summaries:
        lines.append(f"{rs['dataset']}/{rs['config']}: micro_ocr_acc={_fmt_pct(rs['micro_ocr_accuracy'])}  "
                      f"micro_confirmed_acc={_fmt_pct(rs['micro_confirmed_accuracy'])}  "
                      f"confidently_wrong_confirmations={rs['confidently_wrong_confirmations']}")
    return "\n".join(lines)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    records: List[dict] = []
    records += run_sweep("A", DATASET_A_DIR, DATASET_A_GT, exclude_from_scoring=set())
    records += run_sweep("B", DATASET_B_DIR, DATASET_B_GT, exclude_from_scoring=DATASET_B_EXCLUDE_FROM_SCORING)

    with open(os.path.join(OUT_DIR, "m5_1_raw_records.json"), "w") as f:
        json.dump(records, f, indent=2, default=str)

    sweep_summary = aggregate(records)
    with open(os.path.join(OUT_DIR, "m5_1_sweep_summary.json"), "w") as f:
        json.dump(sweep_summary, f, indent=2)

    reconcile_summaries = []
    for dataset in ("A", "B"):
        for config in ("production", "no_whitelist"):
            rs = run_reconcile_replay(records, dataset, config)
            reconcile_summaries.append(rs)
            with open(os.path.join(OUT_DIR, f"m5_1_reconcile_{dataset}_{config}.json"), "w") as f:
                json.dump(rs, f, indent=2)

    summary_text = render_text_summary(sweep_summary, reconcile_summaries)
    with open(os.path.join(OUT_DIR, "m5_1_summary.txt"), "w") as f:
        f.write(summary_text + "\n")

    print(summary_text)
    print(f"\nWrote reports to {OUT_DIR}/")


if __name__ == "__main__":
    main()
