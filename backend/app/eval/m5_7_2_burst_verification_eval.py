"""M5.7.2: measures multi-frame BURST verification (app.pipeline.
burst_verify) against real Dataset A/B, comparing it to today's single-frame
Calibration Verify behaviour -- required before this milestone's change can
be declared successful, not optional polish.

METHOD. Dataset A/B are sparse PHOTOGRAPHED STILLS, not video -- there is no
literal "5 consecutive real camera frames" available on disk. Any honest
measurement of "does averaging over a burst help under transient noise" has
to synthesize the burst, so this script does that EXPLICITLY and says so:
for each (sample, vital) evaluation point it builds N independently-seeded
degraded variants of that sample's frame using the SAME, already-validated
degradation primitives the rest of this codebase uses for exactly this
purpose (simulator.randomize.augment's apply_glare/apply_dim/apply_blur/
apply_noise) -- reused unmodified, not reimplemented. Perspective and
occlusion are deliberately EXCLUDED from the composition: a burst is several
frames captured moments apart from a STATIC setup (the operator's hand and
the monitor are not moving during a ~1s Verify capture), so only
appearance-level noise (glare/exposure/blur/compression) is in scope here --
geometry is a different, already-covered concern (localization IoU,
app.eval.m5_2_calibration_eval).

Calibration profiles are built via app.eval.m5_2_calibration_eval's own
_build_profile_from_earliest_frames (imported, not duplicated) with 20%
width padding -- matching production's WIDTH_SAFETY_PAD_FRACTION, which
Calibration's real /verify and /verify-burst endpoints both apply.

THREE conditions compared per (sample, vital), at TWO noise levels (clean --
a regression check against today's already-good-frame behaviour -- and
noisy -- the actual scenario this milestone targets):
  - single  : today's production Verify -- ONE frame, ONE OCR read, no
              confidence gate at all (matches app.api.calibration.
              verify_candidate's real behaviour exactly).
  - burst70 : app.pipeline.burst_verify.verify_burst with its SHIPPED
              default confidence floor (BURST_CONFIDENCE_FLOOR ==
              CONFIDENCE_MEDIUM_MIN, 70).
  - burst40 : the SAME burst mechanism with the alternate floor considered
              in burst_verify.py's own docstring (CONFIDENCE_TEMPORAL_FLOOR,
              40) -- run side by side, not assumed, so the choice between
              them is a measured decision.

Reports per condition: success/stable rate, CORRECT-read rate, WRONG-read
rate (the safety-critical number), confidence distribution, latency, and
whether the CLEAN-frame condition regresses relative to single-frame
production behaviour.

Usage:
    python -m app.eval.m5_7_2_burst_verification_eval [--frames-per-burst N]
"""

import argparse
import json
import os
import random
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from app.eval.harness import load_dataset
from app.eval.m5_2_calibration_eval import (
    DATASET_A_DIR,
    DATASET_A_GT,
    DATASET_B_DIR,
    DATASET_B_GT,
    DATASET_B_EXCLUDE_FROM_SCORING,
    VITALS,
    _build_profile_from_earliest_frames,
    _gt_for_sample,
    _round_display,
)
from app.models.calibration import CalibrationProfile
from app.pipeline.burst_verify import BURST_CONFIDENCE_FLOOR, _evaluate, collect_all_samples
from app.pipeline.calibrated_roi import extract_rois_from_boxes
from app.pipeline.ocr import NibpValue, TesseractEngine, _locate_tesseract_binary
from app.validation.temporal import CONFIDENCE_TEMPORAL_FLOOR
from simulator.randomize.augment import apply_blur, apply_dim, apply_glare, apply_noise

import pytesseract

resolved = _locate_tesseract_binary(None)
if resolved:
    pytesseract.pytesseract.tesseract_cmd = resolved

ENGINE = TesseractEngine()
OUT_DIR = "app/eval/tier2_data/m5_7_2_report"

FIELD_TO_VITAL: Dict[str, str] = {
    "hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr",
    "nibpSystolic": "nibp", "nibpDiastolic": "nibp", "nibpMean": "nibp",
}
FIELD_DECIMALS = {"hr": 0, "spo2": 0, "nibpSystolic": 0, "nibpDiastolic": 0, "nibpMean": 0, "etco2": 0, "temp": 1, "rr": 0}

# Appearance-only noise composition -- see module docstring for why
# perspective/occlusion are excluded. Probabilities/severity ranges mirror
# simulator.randomize.augment's own "light"/"heavy" level presets for these
# same four effects (not invented for this script).
_NOISE_PRESETS = {
    "light": {"glare": 0.25, "dim": 0.25, "blur": 0.35, "noise": 0.50, "severity": (0.15, 0.45)},
    "heavy": {"glare": 0.55, "dim": 0.50, "blur": 0.55, "noise": 0.75, "severity": (0.55, 1.0)},
}


def _apply_burst_noise(img: np.ndarray, rng: random.Random, level: str) -> np.ndarray:
    """One independently-seeded transient-noise variant of `img`, standing
    in for one real camera capture in a burst -- see module docstring."""
    if level == "clean":
        return img
    preset = _NOISE_PRESETS[level]
    lo, hi = preset["severity"]
    out = img
    if rng.random() < preset["glare"]:
        out, _ = apply_glare(out, rng, severity=rng.uniform(lo, hi))
    if rng.random() < preset["dim"]:
        out, _ = apply_dim(out, rng, severity=rng.uniform(lo, hi))
    if rng.random() < preset["blur"]:
        out, _ = apply_blur(out, rng, severity=rng.uniform(lo, hi))
    if rng.random() < preset["noise"]:
        out, _ = apply_noise(out, rng, severity=rng.uniform(lo, hi))
    return out


def _crop_for_vital(frame: np.ndarray, profile: CalibrationProfile, vital: str) -> Optional[np.ndarray]:
    box = profile.roi_boxes.get(vital)
    if box is None:
        return None
    result = extract_rois_from_boxes(frame, {vital: box})[vital]
    return result.crop if result is not None else None


def _single_frame_result(crop: Optional[np.ndarray], vital: str) -> Tuple[dict, float, float]:
    """Reproduces app.api.calibration.verify_candidate's real behaviour
    exactly: ONE OCR read, NO confidence gate -- whatever Tesseract returns
    is what today's operator sees. Returns (parsed_fields, confidence, latency_s)."""
    if crop is None:
        group_fields = ["nibpSystolic", "nibpDiastolic", "nibpMean"] if vital == "nibp" else [vital]
        return {f: None for f in group_fields}, 0.0, 0.0
    t0 = time.perf_counter()
    value, confidence = ENGINE.read_vital(crop, vital)
    latency_s = time.perf_counter() - t0
    if vital == "nibp":
        assert isinstance(value, NibpValue)
        parsed = {"nibpSystolic": value.systolic, "nibpDiastolic": value.diastolic, "nibpMean": value.mean}
    else:
        parsed = {vital: value}
    return parsed, confidence, latency_s


def _finalize_burst(
    primary: list, secondary: list, has_crops: bool, confidence_floor: float
) -> Tuple[object, bool, float, float, bool]:
    """Same decision logic as burst_verify._aggregate_field, but taking
    ALREADY-COLLECTED primary/secondary samples so this eval script can
    compare several confidence floors against ONE set of OCR results
    instead of re-running Tesseract per floor -- see collect_all_samples's
    docstring. Reuses the REAL _evaluate() (the actual stability/gating
    decision), never reimplements it -- only the "which sample list did we
    need" bookkeeping around it is eval-script-local.
    Returns (value_or_None, stable, confidence, agreement_pct, used_variant)."""
    stable, _recovered, _reason, mode_key, mode_samples, all_valid = _evaluate(primary, confidence_floor)
    used_variant = False
    if not stable and has_crops:
        used_variant = True
        stable, _recovered, _reason, mode_key, mode_samples, all_valid = _evaluate(
            primary + secondary, confidence_floor
        )
    total_valid = len(all_valid)
    agreement = (len(mode_samples) / total_valid * 100) if total_valid else 0.0
    if not mode_samples:
        return None, False, 0.0, 0.0, used_variant
    rep = mode_samples[0]
    confidence = sum(s.confidence for s in mode_samples) / len(mode_samples)
    return (rep.original_value if stable else None), stable, confidence, agreement, used_variant


def _burst_to_fields(vital: str, value: object) -> dict:
    if vital == "nibp":
        if isinstance(value, NibpValue):
            return {"nibpSystolic": value.systolic, "nibpDiastolic": value.diastolic, "nibpMean": value.mean}
        return {"nibpSystolic": None, "nibpDiastolic": None, "nibpMean": None}
    return {vital: value}


def run_dataset(
    dataset_name: str, dataset_dir: str, gt_path: str, exclude_from_scoring: set, frames_per_burst: int
) -> dict:
    with open(gt_path) as f:
        gt_all = json.load(f)["values"]

    samples = load_dataset(dataset_dir)
    profile, calib_ids = _build_profile_from_earliest_frames(samples, width_pad_fraction=0.20)
    print(f"[{dataset_name}] {len(samples)} samples, calibrated per-vital from: {calib_ids}")

    records: List[dict] = []

    for s in samples:
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        gt_values = _gt_for_sample(gt_all, s["id"])

        for vital in VITALS:
            if calib_ids.get(vital) == s["id"]:
                continue  # never evaluate on the exact frame calibrated from
            if vital not in profile.roi_boxes:
                continue
            group_fields = ["nibpSystolic", "nibpDiastolic", "nibpMean"] if vital == "nibp" else [vital]
            if all(gt_values[f] is None for f in group_fields):
                continue

            for level in ("clean", "light"):
                seed_base = hash((s["id"], vital, level)) & 0xFFFFFFFF

                # ── single-frame (today's production behaviour) ──
                single_frame = _apply_burst_noise(img.copy(), random.Random(seed_base), level)
                single_crop = _crop_for_vital(single_frame, profile, vital)
                single_parsed, single_conf, single_latency = _single_frame_result(single_crop, vital)

                # ── burst (N independently-noised frames) -- OCR run ONCE per
                # variant, both floors evaluated against the SAME results ──
                burst_frames = [
                    _apply_burst_noise(img.copy(), random.Random(seed_base + 1 + i), level)
                    for i in range(frames_per_burst)
                ]
                burst_crops = [c for c in (_crop_for_vital(f, profile, vital) for f in burst_frames) if c is not None]
                t0 = time.perf_counter()
                primary, secondary = collect_all_samples(ENGINE, burst_crops, vital)
                shared_ocr_latency = time.perf_counter() - t0
                has_crops = bool(burst_crops)

                b70_value, b70_stable, b70_conf, b70_agree, b70_variant = _finalize_burst(
                    primary, secondary, has_crops, BURST_CONFIDENCE_FLOOR)
                b40_value, b40_stable, b40_conf, b40_agree, b40_variant = _finalize_burst(
                    primary, secondary, has_crops, CONFIDENCE_TEMPORAL_FLOOR)

                for field in group_fields:
                    gt = gt_values[field]
                    if gt is None:
                        continue
                    decimals = FIELD_DECIMALS[field]
                    gt_disp = _round_display(gt, decimals)
                    excluded = field in exclude_from_scoring or FIELD_TO_VITAL[field] in exclude_from_scoring

                    single_pred = _round_display(single_parsed.get(field), decimals) if single_parsed.get(field) is not None else None
                    b70_fields = _burst_to_fields(vital, b70_value)
                    b40_fields = _burst_to_fields(vital, b40_value)
                    b70_pred = _round_display(b70_fields.get(field), decimals) if b70_fields.get(field) is not None else None
                    b40_pred = _round_display(b40_fields.get(field), decimals) if b40_fields.get(field) is not None else None

                    records.append({
                        "dataset": dataset_name, "id": s["id"], "vital": vital, "field": field, "level": level,
                        "ground_truth": gt_disp, "excluded_from_scoring": excluded,
                        "single": {
                            "predicted": single_pred, "missing": single_pred is None,
                            "correct": None if excluded else (single_pred is not None and single_pred == gt_disp),
                            "confidence": single_conf, "latency_s": single_latency,
                        },
                        "burst70": {
                            "predicted": b70_pred, "stable": b70_stable, "missing": b70_pred is None,
                            "correct": None if excluded else (b70_pred is not None and b70_pred == gt_disp),
                            "confidence": b70_conf, "agreement_pct": b70_agree,
                            "used_variant": b70_variant, "latency_s": shared_ocr_latency / len(group_fields),
                        },
                        "burst40": {
                            "predicted": b40_pred, "stable": b40_stable, "missing": b40_pred is None,
                            "correct": None if excluded else (b40_pred is not None and b40_pred == gt_disp),
                            "confidence": b40_conf, "agreement_pct": b40_agree,
                            "used_variant": b40_variant, "latency_s": shared_ocr_latency / len(group_fields),
                        },
                    })

    return {"dataset": dataset_name, "calibration_frames_per_vital": calib_ids, "records": records}


def aggregate(records: List[dict], condition: str, level: Optional[str] = None) -> dict:
    """WRONG must mean 'produced a non-null value that does not match ground
    truth' -- the safety-critical number -- NOT 'did not produce the correct
    value', which also (incorrectly) counts every honestly-reported missing/
    unstable field as though it had confidently stated something wrong. A
    system that says 'I don't know' 90% of the time and is never wrong the
    other 10% is much safer than one that guesses wrong 30% of the time,
    and conflating the two would hide exactly the distinction this
    milestone's safety requirement cares about."""
    scored = [r for r in records if not r["excluded_from_scoring"] and (level is None or r["level"] == level)]
    n = len(scored)
    vals = [r[condition] for r in scored]
    correct = [v for v in vals if v["correct"]]
    wrong = [v for v in vals if (not v["missing"]) and v["correct"] is False]
    missing_or_unstable = [v for v in vals if v["missing"]]
    confidences = [v["confidence"] for v in vals if not v["missing"]]
    latencies = [v["latency_s"] for v in vals]

    per_vital: Dict[str, dict] = {}
    for vital in VITALS:
        v_scored = [r for r in scored if r["vital"] == vital]
        v_vals = [r[condition] for r in v_scored]
        v_correct = [v for v in v_vals if v["correct"]]
        v_wrong = [v for v in v_vals if (not v["missing"]) and v["correct"] is False]
        v_missing = [v for v in v_vals if v["missing"]]
        per_vital[vital] = {
            "n": len(v_scored),
            "correct_rate": (len(v_correct) / len(v_scored)) if v_scored else None,
            "wrong_rate": (len(v_wrong) / len(v_scored)) if v_scored else None,
            "missing_or_unstable_rate": (len(v_missing) / len(v_scored)) if v_scored else None,
        }

    return {
        "condition": condition, "level": level, "n": n,
        "success_rate": ((n - len(missing_or_unstable)) / n) if n else None,  # "produced a value at all" (stable, for burst)
        "correct_rate": (len(correct) / n) if n else None,
        "wrong_rate": (len(wrong) / n) if n else None,  # non-null AND incorrect -- the safety-critical number
        "missing_or_unstable_rate": (len(missing_or_unstable) / n) if n else None,
        "mean_confidence": (sum(confidences) / len(confidences)) if confidences else None,
        "confidence_buckets": _bucket(confidences),
        "mean_latency_ms": (sum(latencies) / len(latencies) * 1000) if latencies else None,
        "per_vital": per_vital,
    }


def _bucket(confidences: List[float]) -> Dict[str, int]:
    buckets = {"0-40": 0, "40-70": 0, "70-90": 0, "90-100": 0}
    for c in confidences:
        if c < 40:
            buckets["0-40"] += 1
        elif c < 70:
            buckets["40-70"] += 1
        elif c < 90:
            buckets["70-90"] += 1
        else:
            buckets["90-100"] += 1
    return buckets


def render_text_summary(all_records: List[dict]) -> str:
    lines = ["=== M5.7.2 burst verification eval ===", ""]
    for condition in ("single", "burst70", "burst40"):
        lines.append(f"--- condition: {condition} ---")
        for level in ("clean", "light"):
            agg = aggregate(all_records, condition, level)
            if agg["n"] == 0:
                continue
            lines.append(
                f"  [{level:5s}] n={agg['n']:4d}  success={_pct(agg['success_rate'])}  "
                f"correct={_pct(agg['correct_rate'])}  WRONG={_pct(agg['wrong_rate'])}  "
                f"missing/unstable={_pct(agg['missing_or_unstable_rate'])}  "
                f"mean_conf={agg['mean_confidence']}  latency={agg['mean_latency_ms']:.1f}ms"
                if agg['mean_latency_ms'] is not None else ""
            )
        lines.append("")

    lines.append("--- regression check: clean-frame condition vs single-frame production ---")
    single_clean = aggregate(all_records, "single", "clean")
    for condition in ("burst70", "burst40"):
        burst_clean = aggregate(all_records, condition, "clean")
        lines.append(
            f"  single/clean correct={_pct(single_clean['correct_rate'])} wrong={_pct(single_clean['wrong_rate'])}  vs  "
            f"{condition}/clean correct={_pct(burst_clean['correct_rate'])} wrong={_pct(burst_clean['wrong_rate'])}"
        )
    lines.append("")
    return "\n".join(lines)


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-per-burst", type=int, default=5)
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    results = [
        run_dataset("A", DATASET_A_DIR, DATASET_A_GT, set(), args.frames_per_burst),
        run_dataset("B", DATASET_B_DIR, DATASET_B_GT, DATASET_B_EXCLUDE_FROM_SCORING, args.frames_per_burst),
    ]
    all_records = [r for res in results for r in res["records"]]

    with open(os.path.join(OUT_DIR, "m5_7_2_raw_records.json"), "w") as f:
        json.dump(all_records, f, indent=2, default=str)

    summary: dict = {"frames_per_burst": args.frames_per_burst, "by_condition_level": {}}
    for condition in ("single", "burst70", "burst40"):
        for level in ("clean", "light"):
            summary["by_condition_level"][f"{condition}/{level}"] = aggregate(all_records, condition, level)
    with open(os.path.join(OUT_DIR, "m5_7_2_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    text = render_text_summary(all_records)
    with open(os.path.join(OUT_DIR, "m5_7_2_summary.txt"), "w") as f:
        f.write(text + "\n")
    print(text)
    print(f"Wrote reports to {OUT_DIR}/")


if __name__ == "__main__":
    main()
