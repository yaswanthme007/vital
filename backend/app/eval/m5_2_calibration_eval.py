"""M5.2 Phase 8/9: evaluates the CALIBRATED ROI path (app.pipeline.
calibrated_roi, "calibrated box, no tracking") against real Dataset A/B
photos -- NOT oracle ground-truth crops (that was M5.1's job; see
app.eval.m5_1_ocr_config_sweep). This is the thing M5.1 explicitly could not
measure: what happens when localization comes from an operator's ONE-TIME
calibration instead of a hand-drawn-per-frame ground-truth box.

METHOD. For each dataset:
  1. CALIBRATE once, on the dataset's first frame: take that frame's
     annotated ground-truth boxes (== what an operator would draw --
     exactly EVIDENCE.md sec 6's own definition of the calibration
     counterfactual) and normalize them by that frame's pixel dimensions
     into a CalibrationProfile via app.pipeline.calibrated_roi (the real,
     production module -- not reimplemented).
  2. EVALUATE every OTHER frame in the dataset by mapping that ONE fixed
     profile onto it (app.pipeline.calibrated_roi.make_extractor) -- no
     per-frame homography, no re-detection, no tracking. This is the
     literal "calibrated box, no tracking" row EVIDENCE.md sec 6 measured;
     this script is the COMMITTED, reproducible version of that measurement
     (EVIDENCE.md sec 10 lists committed-script equivalents as the standard
     this codebase holds every cited number to).
  3. Three separate signals are recorded per (frame, vital), never
     collapsed into one opaque number (Phase 8's explicit instruction):
       - LOCALIZATION: IoU between the calibrated box (mapped onto this
         frame) and this frame's OWN ground-truth box, if this frame has
         one annotated for this vital.
       - OCR: app.pipeline.ocr.TesseractEngine (production, M5.1's config,
         imported unmodified) run on the calibrated crop.
       - RECONCILE: the real, imported app.validation.reconcile.reconcile(),
         replayed in chronological (sample-id) order -- identical pattern to
         m5_1_ocr_config_sweep.run_reconcile_replay / m4_3_reliability.
         replay_reconcile().
  4. Phase 9 cross-frame stability: since M5.2 has no tracking, the box
     itself never moves between frames -- ALL of the localization IoU's
     frame-to-frame variation comes from the monitor/camera having moved
     relative to that fixed box. Reported as IoU-over-time per vital, with
     the explicit caveat these datasets are sparse stills, not continuous
     video (EVIDENCE.md sec 8) -- true camera-motion robustness needs M5.3's
     dense-frame extraction, not this data.

Nothing here trains a model, tunes a threshold, or modifies a dataset.

Usage:
    python -m app.eval.m5_2_calibration_eval
"""

import json
import os
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from app.eval.harness import load_dataset
from app.eval.tier2_common import Box, iou
from app.models.calibration import CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import extract_rois_from_boxes, make_extractor
from app.pipeline.ocr import NibpValue, TesseractEngine, _locate_tesseract_binary
from app.validation.reconcile import initial_confirmed_state, reconcile
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

OUT_DIR = "app/eval/tier2_data/m5_2_report"

# Same exclusions M5.1 used, same reasons (EVIDENCE.md sec 9): Dataset B's
# Temp GT box is clipped and its value sits outside RANGE_BOUNDS regardless
# of any localization or OCR quality -- genuinely no-data, not a failure of
# this milestone's mechanism. NIBP never appears in Dataset B at all.
DATASET_B_EXCLUDE_FROM_SCORING = {"temp"}

ENGINE = TesseractEngine()


def _gt_box_for_vital(label: dict, vital: str) -> Optional[Box]:
    """Raw, UN-warped pixel box straight from the annotation -- deliberately
    NOT run through detect_screen()'s homography. detect_screen() fires
    0/52 on Dataset A and 0/17 on Dataset B (ARCHITECTURE.md/EVIDENCE.md
    sec 2) -- every GT box in both datasets is therefore already being used
    in raw-frame pixel space by every other M4/M5 script that touches this
    data (the homography step is always a no-op here in practice). This
    script makes that explicit rather than calling detect_screen() for a
    transform that never fires."""
    raw = label.get("rois", {}).get(vital)
    if not raw or raw[2] <= 0 or raw[3] <= 0:
        return None
    return tuple(raw)


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


def _round_display(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, decimals) if decimals else float(round(value))


def _build_profile_from_earliest_frames(
    samples: List[dict], width_pad_fraction: float = 0.0
) -> Tuple[CalibrationProfile, Dict[str, str]]:
    """Phase 8: 'create a calibration profile from an initial frame/frame
    set'. Both datasets' ground-truth annotation is itself sparse per
    sample -- e.g. Dataset A's sample_0001 is only boxed for nibp/temp, not
    all six vitals (confirmed by inspection, not assumed) -- an artifact of
    how these labels were built, not evidence a real operator's live
    calibration session couldn't see every field simultaneously. So each
    vital's ROI is taken from the EARLIEST (lowest sample id) frame that
    happens to have a GT box for it, one lookup per vital -- this is the
    closest honest stand-in for "the operator drew all six visible fields
    during one calibration pass" that this dataset's annotation coverage
    actually supports, without cherry-picking a specific favourable sample.
    A vital annotated nowhere in the dataset (Dataset B's NIBP, never
    populated on screen at all -- EVIDENCE.md sec 9) is simply absent from
    the profile, exactly as an operator who can't see that field would
    leave it undrawn.

    width_pad_fraction (0.0 default): a stand-in for the ROADMAP/ARCHITECTURE
    operator guidance "draw the field's display SLOT, not the current
    digits" (Phase 2 -- a live operator sees the full slot; a single
    annotated GT box only ever captures whatever digit COUNT happened to be
    on screen at that instant, understating the slot). 0.0 reproduces the
    literal annotated box, which this milestone's own eval below shows
    reproduces EVIDENCE.md sec 6.1's documented "too tight" hazard
    (confidently-wrong confirmations on HR/RR, Dataset A) when a vital's
    earliest annotated frame happens to show a short digit count. A second
    pass with padding > 0 is run and reported alongside it -- see
    docs/M5_2_REAL_CALIBRATION_REPORT.md sec 9 for both numbers and why
    geometry-only padding is a PARTIAL mitigation, not a substitute for the
    Verify gate (EVIDENCE.md sec 6.1 already found 50% padding only
    recovers Dataset A HR to 60%, still well below the 95% oracle ceiling).

    Returns (profile, {vital: calibration_sample_id}) -- the per-vital
    source id lets run_dataset() exclude a vital from evaluation on the ONE
    frame it was calibrated from, without over-excluding frames that
    supplied a DIFFERENT vital's calibration box."""
    calib_ids: Dict[str, str] = {}
    roi_boxes: Dict[str, NormalizedBox] = {}
    ref_w = ref_h = None

    for sample in samples:  # already sorted by id (app.eval.harness.load_dataset)
        for vital in VITALS:
            if vital in roi_boxes:
                continue
            box = _gt_box_for_vital(sample["label"], vital)
            if box is None:
                continue
            img = np.array(Image.open(sample["png_path"]).convert("RGB"))
            h, w = img.shape[:2]
            if ref_w is None:
                ref_w, ref_h = w, h
            x, y, bw, bh = box
            if width_pad_fraction > 0:
                # Grow the slot symmetrically (both edges) so a short digit
                # count today doesn't cap a longer one later -- centered, not
                # anchored to one edge, since this dataset's annotations
                # don't record which side the display right/left-aligns to.
                pad = bw * width_pad_fraction / 2
                x, bw = x - pad, bw + 2 * pad
            # Normalize against THIS sample's own dimensions -- if frames in
            # a dataset ever differ in resolution, each box is still
            # correctly proportioned to the frame it was actually drawn on.
            nx, ny = max(0.0, x / w), max(0.0, y / h)
            nw = min(1.0 - nx, bw / w)
            nh = min(1.0 - ny, bh / h)
            roi_boxes[vital] = NormalizedBox(x=nx, y=ny, w=nw, h=nh)
            calib_ids[vital] = sample["id"]
        if len(roi_boxes) == len(VITALS):
            break

    profile = CalibrationProfile(
        id="eval-profile",
        reference_width=ref_w,
        reference_height=ref_h,
        roi_boxes=roi_boxes,
        created_at=0,
        updated_at=0,
    )
    return profile, calib_ids


def run_dataset(
    dataset_name: str, dataset_dir: str, gt_path: str, exclude_from_scoring: set, width_pad_fraction: float = 0.0
) -> dict:
    with open(gt_path) as f:
        gt_all = json.load(f)["values"]

    samples = load_dataset(dataset_dir)
    print(f"[{dataset_name}] loaded {len(samples)} samples")

    profile, calib_ids = _build_profile_from_earliest_frames(samples, width_pad_fraction=width_pad_fraction)
    extractor = make_extractor(profile)
    print(f"[{dataset_name}] calibrated per-vital from: {calib_ids} (width_pad={width_pad_fraction})")

    localization_records: List[dict] = []
    ocr_records: List[dict] = []
    n_eval_frames = 0

    for s in samples:
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        h, w = img.shape[:2]
        gt_values = _gt_for_sample(gt_all, s["id"])

        t0 = time.perf_counter()
        rois = extractor(img)
        extract_latency_s = time.perf_counter() - t0
        n_eval_frames += 1

        for vital in VITALS:
            if calib_ids.get(vital) == s["id"]:
                continue  # never evaluate a vital on the exact frame it was calibrated from
            gt_box = _gt_box_for_vital(s["label"], vital)
            roi_result = rois.get(vital)

            # ── Localization: calibrated box vs this frame's OWN ground truth ──
            if gt_box is not None and vital in profile.roi_boxes:
                nb = profile.roi_boxes[vital]
                predicted_box: Box = (nb.x * w, nb.y * h, nb.w * w, nb.h * h)
                box_iou = iou(tuple(float(c) for c in predicted_box), tuple(float(c) for c in gt_box))
                localization_records.append({"dataset": dataset_name, "id": s["id"], "vital": vital, "iou": box_iou})

            # ── OCR: on the calibrated crop, production TesseractEngine ──
            group_fields = ["nibpSystolic", "nibpDiastolic", "nibpMean"] if vital == "nibp" else [vital]
            if all(gt_values[f] is None for f in group_fields):
                continue  # nothing to score this frame for this vital

            if roi_result is None:
                parsed = {f: None for f in group_fields}
                confidence = 0.0
                latency_s = 0.0
            else:
                t0 = time.perf_counter()
                value, confidence = ENGINE.read_vital(roi_result.crop, vital)
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
                ocr_records.append({
                    "dataset": dataset_name,
                    "id": s["id"],
                    "vital": vital,
                    "field": field,
                    "ground_truth": gt_disp,
                    "predicted": pred_disp,
                    "missing": missing,
                    "correct": None if excluded else correct,
                    "excluded_from_scoring": excluded,
                    "confidence": confidence,
                    "ocr_latency_s": latency_s,
                    "extract_latency_s": extract_latency_s,
                })

    return {
        "dataset": dataset_name,
        "calibration_frames_per_vital": calib_ids,
        "calibrated_vitals": sorted(profile.roi_boxes),
        "n_eval_frames": n_eval_frames,
        "localization_records": localization_records,
        "ocr_records": ocr_records,
    }


# ─── aggregation ──────────────────────────────────────────────────────────


def _clears_gate(confidence: float) -> bool:
    return confidence >= CONFIDENCE_MEDIUM_MIN


def aggregate_localization(records: List[dict]) -> dict:
    by_vital: Dict[str, List[float]] = {}
    for r in records:
        by_vital.setdefault(r["vital"], []).append(r["iou"])
    out = {}
    for vital, ious in by_vital.items():
        out[vital] = {
            "n": len(ious),
            "mean_iou": sum(ious) / len(ious) if ious else None,
            "min_iou": min(ious) if ious else None,
            "max_iou": max(ious) if ious else None,
            "recall_at_0.3": sum(1 for i in ious if i >= 0.3) / len(ious) if ious else None,
            "recall_at_0.5": sum(1 for i in ious if i >= 0.5) / len(ious) if ious else None,
            "iou_timeline": ious,  # Phase 9: cross-frame stability, in sample order
        }
    all_ious = [r["iou"] for r in records]
    out["_overall"] = {
        "n": len(all_ious),
        "mean_iou": sum(all_ious) / len(all_ious) if all_ious else None,
        "recall_at_0.3": sum(1 for i in all_ious if i >= 0.3) / len(all_ious) if all_ious else None,
        "recall_at_0.5": sum(1 for i in all_ious if i >= 0.5) / len(all_ious) if all_ious else None,
    }
    return out


def aggregate_ocr(records: List[dict]) -> dict:
    scored = [r for r in records if not r["excluded_from_scoring"]]
    n = len(scored)
    correct = [r for r in scored if r["correct"]]
    missing = [r for r in scored if r["missing"]]
    correct_conf = [r["confidence"] for r in correct]

    per_vital: Dict[str, dict] = {}
    for vital in VITALS:
        v_scored = [r for r in scored if r["vital"] == vital]
        v_correct = [r for r in v_scored if r["correct"]]
        per_vital[vital] = {
            "n": len(v_scored),
            "accuracy": (len(v_correct) / len(v_scored)) if v_scored else None,
            "missing": sum(1 for r in v_scored if r["missing"]),
        }

    latencies = [r["ocr_latency_s"] + r["extract_latency_s"] for r in records]
    return {
        "n_scored": n,
        "accuracy": (len(correct) / n) if n else None,
        "missing_rate": (len(missing) / n) if n else None,
        "mean_confidence_on_correct": (sum(correct_conf) / len(correct_conf)) if correct_conf else None,
        "pct_correct_clearing_gate": (sum(1 for c in correct_conf if _clears_gate(c)) / len(correct_conf)) if correct_conf else None,
        "per_vital": per_vital,
        "mean_latency_ms": (sum(latencies) / len(latencies)) * 1000 if latencies else None,
    }


def run_reconcile_replay(ocr_records: List[dict], dataset: str, interval_ms: int = 1000, base_ts: int = 1_700_000_000_000) -> dict:
    """Identical replay pattern to m5_1_ocr_config_sweep.run_reconcile_replay,
    fed the CALIBRATED-path OCR records instead of oracle-crop ones."""
    by_id: Dict[str, Dict[str, dict]] = {}
    for r in ocr_records:
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
        reading, confirmed, _flagged = reconcile(raw_reading, per_vital_confidence, confirmed)

        frame_entry = {"id": sid, "fields": {}}
        for field in FIELDS:
            rec = field_records.get(field)
            if rec is None or rec["excluded_from_scoring"]:
                continue
            gt = rec["ground_truth"]
            if gt is None:
                continue
            gt_for_compare = normalize_temp_celsius(gt) if field == "temp" else gt
            gt_for_compare = _round_display(gt_for_compare, FIELD_DECIMALS[field]) if field == "temp" else gt_for_compare
            confirmed_disp = _round_display(reading[field], FIELD_DECIMALS[field])
            raw_ocr_for_compare = (
                _round_display(normalize_temp_celsius(rec["predicted"]), FIELD_DECIMALS[field])
                if field == "temp" and rec["predicted"] is not None
                else rec["predicted"]
            )

            raw_value = raw_reading.get(field)
            if field == "temp" and raw_value is not None:
                raw_value = normalize_temp_celsius(raw_value)
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
                "ocr_correct": rec["correct"],
                "confidence": rec["confidence"],
                "reason": reason,
                "confirmed_value": confirmed_disp,
                "confirmed_correct": confirmed_disp == gt_for_compare,
                "raw_ocr_for_compare": raw_ocr_for_compare,
            }
        timeline.append(frame_entry)

    field_stats: Dict[str, dict] = {
        f: {"n": 0, "ocr_correct": 0, "confirmed_correct": 0, "wrong_but_confirmed": 0} for f in FIELDS
    }
    for frame in timeline:
        for field, entry in frame["fields"].items():
            st = field_stats[field]
            st["n"] += 1
            if entry["ocr_correct"]:
                st["ocr_correct"] += 1
            if entry["confirmed_correct"]:
                st["confirmed_correct"] += 1
            if (
                (not entry["ocr_correct"])
                and entry["confirmed_correct"] is False
                and entry["confirmed_value"] == entry["raw_ocr_for_compare"]
                and entry["raw_ocr_for_compare"] is not None
            ):
                st["wrong_but_confirmed"] += 1

    total_n = sum(v["n"] for v in field_stats.values())
    total_ocr_correct = sum(v["ocr_correct"] for v in field_stats.values())
    total_confirmed_correct = sum(v["confirmed_correct"] for v in field_stats.values())
    total_wrong_but_confirmed = sum(v["wrong_but_confirmed"] for v in field_stats.values())

    return {
        "dataset": dataset,
        "n_frames": len(timeline),
        "field_stats": field_stats,
        "micro_ocr_accuracy": (total_ocr_correct / total_n) if total_n else None,
        "micro_confirmed_accuracy": (total_confirmed_correct / total_n) if total_n else None,
        "confidently_wrong_confirmations": total_wrong_but_confirmed,
    }


def render_text_summary(results: List[dict], reconcile_summaries: List[dict]) -> str:
    lines = ["=== M5.2 calibrated-path eval (calibrated box, NO tracking) ===", ""]
    for r in results:
        loc = aggregate_localization(r["localization_records"])
        ocr = aggregate_ocr(r["ocr_records"])
        lines.append(f"[{r['config']}/{r['dataset']}] calibrated per-vital from {r['calibration_frames_per_vital']}, n_eval_frames={r['n_eval_frames']}")
        lines.append(f"  localization: mean_iou={loc['_overall']['mean_iou']:.3f}  "
                      f"recall@0.3={loc['_overall']['recall_at_0.3']*100:.1f}%  recall@0.5={loc['_overall']['recall_at_0.5']*100:.1f}%")
        for vital, v in loc.items():
            if vital == "_overall" or v["n"] == 0:
                continue
            lines.append(f"    {vital:8s} n={v['n']:3d}  mean_iou={v['mean_iou']:.3f}  min={v['min_iou']:.3f}  max={v['max_iou']:.3f}")
        lines.append(f"  OCR (calibrated crops): n={ocr['n_scored']}  acc={ocr['accuracy']*100:.1f}%  "
                      f"missing={ocr['missing_rate']*100:.1f}%  mean_conf_correct={ocr['mean_confidence_on_correct']}  "
                      f"latency={ocr['mean_latency_ms']:.1f}ms")
        for vital, v in ocr["per_vital"].items():
            if v["n"] == 0:
                continue
            lines.append(f"    {vital:8s} n={v['n']:3d}  acc={(v['accuracy'] or 0)*100:.1f}%  missing={v['missing']}")
        lines.append("")

    lines.append("=== reconcile() replay ===")
    for rs in reconcile_summaries:
        lines.append(f"{rs['dataset']}: micro_ocr_acc={(rs['micro_ocr_accuracy'] or 0)*100:.1f}%  "
                      f"micro_confirmed_acc={(rs['micro_confirmed_accuracy'] or 0)*100:.1f}%  "
                      f"confidently_wrong_confirmations={rs['confidently_wrong_confirmations']}")
    return "\n".join(lines)


# Two calibration configs, run and reported side by side (not one silently
# picked): "literal" reproduces the annotated GT box exactly -- the
# no-guidance baseline -- and "padded20" adds 20% symmetric width padding,
# a stand-in for the operator "draw the display slot" guidance
# ROADMAP/ARCHITECTURE call a safety requirement. Comparing the two is the
# point: it is what makes the Verify-gate's necessity a measured finding
# rather than an assertion. See docs/M5_2_REAL_CALIBRATION_REPORT.md sec 9.
CONFIGS = {"literal": 0.0, "padded20": 0.20}


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    all_results = []
    all_reconcile = []
    for config_name, pad in CONFIGS.items():
        results = [
            run_dataset("A", DATASET_A_DIR, DATASET_A_GT, exclude_from_scoring=set(), width_pad_fraction=pad),
            run_dataset(
                "B", DATASET_B_DIR, DATASET_B_GT, exclude_from_scoring=DATASET_B_EXCLUDE_FROM_SCORING, width_pad_fraction=pad
            ),
        ]
        for r in results:
            r["config"] = config_name
            with open(os.path.join(OUT_DIR, f"m5_2_{config_name}_{r['dataset']}_raw.json"), "w") as f:
                json.dump(r, f, indent=2, default=str)
            loc = aggregate_localization(r["localization_records"])
            ocr = aggregate_ocr(r["ocr_records"])
            with open(os.path.join(OUT_DIR, f"m5_2_{config_name}_{r['dataset']}_localization.json"), "w") as f:
                json.dump(loc, f, indent=2)
            with open(os.path.join(OUT_DIR, f"m5_2_{config_name}_{r['dataset']}_ocr.json"), "w") as f:
                json.dump(ocr, f, indent=2)
            all_results.append(r)

        reconcile_summaries = [run_reconcile_replay(r["ocr_records"], f"{config_name}/{r['dataset']}") for r in results]
        for rs in reconcile_summaries:
            with open(os.path.join(OUT_DIR, f"m5_2_reconcile_{config_name}_{rs['dataset'].split('/')[-1]}.json"), "w") as f:
                json.dump(rs, f, indent=2)
            all_reconcile.append(rs)

    summary_text = render_text_summary(all_results, all_reconcile)
    with open(os.path.join(OUT_DIR, "m5_2_summary.txt"), "w") as f:
        f.write(summary_text + "\n")

    print(summary_text)
    print(f"\nWrote reports to {OUT_DIR}/")


if __name__ == "__main__":
    main()
