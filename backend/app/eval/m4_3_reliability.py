"""M4.3: Reliability / validation of PSM10_SELECTIVE through the REAL
production reconciliation state machine.

This is NOT another OCR-preprocessing experiment. M4.1/M4.2/M4.2.1 already
answered "does PSM10_SELECTIVE read digits more accurately than
CURRENT_BASELINE" (yes). This script answers a different question: "once
that OCR output passes through the real app.validation.reconcile.reconcile()
confidence-gating / hold-last-confirmed state machine, does the accuracy
gain actually reach the confirmed, displayed vital -- or does it get
absorbed, or worse, does it let more wrong values through?"

Design (every rule from the M4.3 task honoured explicitly):

  * reconcile() is IMPORTED from app.validation.reconcile, never
    reimplemented. Same for is_in_range/confidence_tier/is_jump_rejected
    (app.validation.rules) and read_frame's own ROI+OCR building blocks
    (app.pipeline.detect.detect_screen, app.pipeline.tier2_roi.
    extract_rois_by_field_classifier, app.pipeline.ocr.TesseractEngine).

  * CURRENT_BASELINE runs through app.pipeline.ocr.TesseractEngine,
    completely unmodified -- production's actual OCR engine class.

  * PSM10_SELECTIVE runs through SelectivePsmEngine below, a subclass that
    exists ONLY in this eval file. It overrides read_vital() to route
    hr/spo2/rr to --psm 10 (M4.2.1's routing table) and delegates
    etco2/temp/nibp to TesseractEngine.read_vital() unchanged (byte-for-byte
    the same call production makes today). This is the "smallest possible
    adapter" the task allows for exercising a production code PATH that
    does not yet exist as a runtime option (the WS endpoint has no per-field
    PSM override hook -- see the M4.3 report's Minimal Production Change
    section) -- app/pipeline/ocr.py itself is NEVER modified.

  * Every crop OCR sees comes from re-running detect_screen() +
    extract_rois_by_field_classifier() live against the real 52 source
    PNGs -- not replayed from M4.1/M4.2's stored JSON -- because that JSON
    only recorded per-field OCR output for "category D" (reaches_ocr)
    vitals. Categories A/B/C/E never had OCR run in that dataset, so
    reusing it here would require GUESSING what read_frame() returns for
    those ticks. Re-running the real pipeline avoids guessing entirely: a
    non-D vital naturally comes back as (None, 0.0) exactly as read_frame()
    would produce it live, and category-E (no GT) vitals still get a real
    OCR attempt -- just excluded from accuracy scoring, never from the
    reconcile() timeline.

  * No ground truth is created or modified. GT values come only from
    m4_ground_truth_values.json (M4.1's manual transcription, 199 entries).
    OCR output is NEVER used as ground truth.

  * Frame timestamps: this dataset (52 frames pulled from one continuous
    video) carries no capture-time metadata anywhere (manifest.json,
    sample_NNNN.json) -- there is no honest way to know the true inter-frame
    spacing. Rather than invent one, this script uses production's OWN
    documented default: app/ws/vitals.py's `interval: float = 1.0` WS query
    param default (i.e. what ReplaySource(mode="pipeline") ticks at when a
    caller doesn't override it). A second full pass at interval=5.0s is
    also run as an explicit sensitivity check, since JUMP_LIMITS' 3.0s
    window means this assumption can change jump-rejection behaviour -- see
    the report's Limitations section for exactly how much it does.

Writes only under
app/eval/tier2_data/external_monitor_video/m4_3_report/. Never touches
production code, the 52 source images/annotations, model artifacts, or
M4.1/M4.2/M4.2.1's own output files.

Usage:
    python -m app.eval.m4_3_reliability
"""

import json
import os
import statistics
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

os.environ.setdefault("ROI_ENGINE", "tier2")

from app.eval.harness import load_dataset
from app.pipeline.detect import detect_screen
from app.pipeline.ocr import TesseractEngine, _locate_tesseract_binary
from app.pipeline.read_frame import read_frame
from app.pipeline.tier2_roi import extract_rois_by_field_classifier
from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile
from app.validation.rules import CONFIDENCE_HIGH_MIN, CONFIDENCE_MEDIUM_MIN, RANGE_BOUNDS, confidence_tier, is_in_range

import pytesseract

resolved = _locate_tesseract_binary(None)
if resolved:
    pytesseract.pytesseract.tesseract_cmd = resolved

DATASET_DIR = "app/eval/tier2_data/external_monitor_video"
GT_PATH = os.path.join(DATASET_DIR, "m4_ocr_report", "m4_ground_truth_values.json")
OUT_DIR = os.path.join(DATASET_DIR, "m4_3_report")

VITAL_GROUPS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")
FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr")
FIELD_TO_GROUP = {
    "hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr",
    "nibpSystolic": "nibp", "nibpDiastolic": "nibp", "nibpMean": "nibp",
}
SCORED_FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "etco2", "temp", "rr")  # matches M4.2.1's ALL_FIELDS (nibpMean excluded from the accuracy table there too)
FIELD_DECIMALS = {"hr": 0, "spo2": 0, "nibpSystolic": 0, "nibpDiastolic": 0, "nibpMean": 0, "etco2": 0, "temp": 1, "rr": 0}

_PSM10_DIGIT_CONFIG = "--psm 10 -c tessedit_char_whitelist=0123456789"
_SELECTIVE_PSM10_VITALS = {"hr", "spo2", "rr"}


class SelectivePsmEngine(TesseractEngine):
    """Eval-only. See module docstring. Never imported by app.pipeline.*."""

    def read_vital(self, crop, vital_type):
        if vital_type in _SELECTIVE_PSM10_VITALS:
            return self._read_scalar(crop, _PSM10_DIGIT_CONFIG, decimal=False)
        return super().read_vital(crop, vital_type)  # etco2/temp/nibp: byte-identical to production


def _round_display(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, decimals) if decimals else float(round(value))


def _gt_for_sample(gt_all: dict, sample_id: str) -> Dict[str, Optional[float]]:
    """Splits the manually-transcribed 'nibp': 'sys/dia/mean' string into
    the 3 numeric fields; every other field is already numeric."""
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


def _read_frame_with_breakdown(img: np.ndarray, engine) -> Tuple[dict, Dict[str, float], Dict[str, float], Dict[str, float]]:
    """Calls the exact same building blocks read_frame() calls (detect_screen,
    extract_rois_by_field_classifier, engine.read_vital), in the same order,
    fusing confidence with the same MIN() read_frame.py uses -- but ALSO
    returns the two pre-fusion signals (classifier_confidence, ocr_confidence)
    separately, which read_frame()'s own return contract discards. This is
    instrumentation only: no decision logic (accept/reject/fuse-how) is
    reimplemented differently from production; extracted purely for Test F's
    confidence-calibration breakdown."""
    screen = detect_screen(img)
    rois = extract_rois_by_field_classifier(screen.image)

    reading: dict = {"hr": None, "spo2": None, "nibpSystolic": None, "nibpDiastolic": None, "nibpMean": None, "etco2": None, "temp": None, "rr": None}
    fused: Dict[str, float] = {}
    classifier_conf: Dict[str, float] = {}
    ocr_conf: Dict[str, float] = {}

    for vital in VITAL_GROUPS:
        roi_result = rois.get(vital)
        if roi_result is None:
            fused[vital] = 0.0
            classifier_conf[vital] = 0.0
            ocr_conf[vital] = 0.0
            continue
        value, ocr_confidence = engine.read_vital(roi_result.crop, vital)
        ocr_conf[vital] = ocr_confidence
        classifier_conf[vital] = roi_result.classifier_confidence if roi_result.classifier_confidence is not None else 0.0
        if roi_result.classifier_confidence is not None:
            fused[vital] = min(roi_result.classifier_confidence, ocr_confidence)  # read_frame.py:151, copied verbatim
        else:
            fused[vital] = ocr_confidence

        if vital == "nibp":
            from app.pipeline.ocr import NibpValue
            if isinstance(value, NibpValue):
                reading["nibpSystolic"] = value.systolic
                reading["nibpDiastolic"] = value.diastolic
                reading["nibpMean"] = value.mean
        else:
            reading[vital] = value

    return reading, fused, classifier_conf, ocr_conf


def run_variant(samples: List[dict], gt_all: dict, engine_name: str, engine) -> List[dict]:
    """Re-runs the real Tier-2 pipeline (detect_screen -> FieldCNN ROI ->
    OCR) live against every one of the 52 source PNGs, in dataset (=
    chronological video) order. Returns one record per sample with the raw
    reading, fused/classifier/ocr confidence, and GT -- everything needed to
    feed reconcile() next."""
    out = []
    for s in samples:
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        t0 = time.perf_counter()
        reading, fused_conf, clf_conf, ocr_conf = _read_frame_with_breakdown(img, engine)
        latency_s = time.perf_counter() - t0
        gt = _gt_for_sample(gt_all, s["id"])
        out.append({
            "id": s["id"],
            "reading": reading,
            "fused_confidence": fused_conf,
            "classifier_confidence": clf_conf,
            "ocr_confidence": ocr_conf,
            "gt": gt,
            "latency_s": latency_s,
        })
        print(f"  [{engine_name}] {s['id']}: " + ", ".join(f"{f}={reading[f]}" for f in ("hr", "spo2", "rr")))
    return out


def replay_reconcile(records: List[dict], interval_ms: int, base_ts: int = 1_700_000_000_000) -> List[dict]:
    """Feeds `records` (chronological) through the REAL, imported reconcile()
    one tick at a time, exactly as app.ws.vitals.send_loop() does -- same
    initial_confirmed_state() seed, same per-call state threading. Returns a
    per-frame, per-field timeline."""
    confirmed = initial_confirmed_state(base_ts)
    timeline = []
    for i, rec in enumerate(records):
        ts = base_ts + i * interval_ms
        raw_reading = dict(rec["reading"])
        raw_reading["timestamp"] = ts
        per_vital_confidence = rec["fused_confidence"]

        prior_snapshot = {f: confirmed[f].value for f in FIELDS}
        reading, confirmed, flagged = reconcile(raw_reading, per_vital_confidence, confirmed)

        frame_entry = {"id": rec["id"], "index": i, "timestamp": ts, "fields": {}}
        for field in FIELDS:
            group = FIELD_TO_GROUP[field]
            raw_value = raw_reading.get(field)
            gt_value = rec["gt"].get(field)
            confirmed_value = reading[field]

            gt_disp = _round_display(gt_value, FIELD_DECIMALS[field])
            raw_disp = _round_display(raw_value, FIELD_DECIMALS[field])
            confirmed_disp = _round_display(confirmed_value, FIELD_DECIMALS[field])

            if raw_value is None:
                ocr_class = "missing"
            elif gt_disp is None:
                ocr_class = "unscored"
            elif raw_disp == gt_disp:
                ocr_class = "correct"
            else:
                ocr_class = "wrong"

            confirmed_correct = None if gt_disp is None else (confirmed_disp == gt_disp)

            # Re-derive WHY reconcile() made the decision it made, using the
            # actual imported rule functions (not reimplemented) purely for
            # reporting -- reconcile() itself already decided; this repeats
            # its own range/jump/tier checks against the same inputs so the
            # timeline can show the mechanism, not just the outcome.
            accepted = confirmed_value == raw_value and raw_value is not None
            reason = None
            if raw_value is None:
                reason = "unreadable"
            elif not is_in_range(field, raw_value):
                reason = "implausible_range"
            else:
                prior_value = prior_snapshot[field]
                # jump check mirrors reconcile()'s own call; elapsed computed the same way
                elapsed = interval_ms / 1000.0 if i > 0 else None
                from app.validation.rules import is_jump_rejected
                if is_jump_rejected(field, raw_value, prior_value, elapsed):
                    reason = "jump_rejected"
                else:
                    tier = confidence_tier(per_vital_confidence.get(group, 0.0))
                    if tier == "ai_low":
                        reason = "low_confidence"
                    elif tier == "ai_medium":
                        reason = "medium_confidence"
                    # else ai_high: reason stays None (clean accept)

            frame_entry["fields"][field] = {
                "gt": gt_disp,
                "raw_ocr": raw_disp,
                "ocr_class": ocr_class,
                "fused_confidence": per_vital_confidence.get(group, 0.0),
                "classifier_confidence": rec["classifier_confidence"].get(group, 0.0),
                "ocr_confidence": rec["ocr_confidence"].get(group, 0.0),
                "accepted": accepted,
                "reason": reason,
                "confirmed_value": confirmed_disp,
                "confirmed_correct": confirmed_correct,
            }
        timeline.append(frame_entry)
    return timeline


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(GT_PATH) as f:
        gt_all = json.load(f)["values"]

    samples = load_dataset(DATASET_DIR)
    print(f"Loaded {len(samples)} samples")

    baseline_engine = TesseractEngine()
    selective_engine = SelectivePsmEngine()

    print("\n=== Running CURRENT_BASELINE over all frames (live pipeline) ===")
    baseline_records = run_variant(samples, gt_all, "BASELINE", baseline_engine)

    print("\n=== Running PSM10_SELECTIVE over all frames (live pipeline) ===")
    selective_records = run_variant(samples, gt_all, "SELECTIVE", selective_engine)

    with open(os.path.join(OUT_DIR, "m4_3_raw_records.json"), "w") as f:
        json.dump({"baseline": baseline_records, "selective": selective_records}, f, indent=2, default=str)
    print(f"\nWrote {os.path.join(OUT_DIR, 'm4_3_raw_records.json')}")

    for interval_ms, tag in ((1000, "interval1000"), (5000, "interval5000")):
        print(f"\n=== Reconcile replay @ {interval_ms}ms/tick ===")
        baseline_timeline = replay_reconcile(baseline_records, interval_ms)
        selective_timeline = replay_reconcile(selective_records, interval_ms)
        with open(os.path.join(OUT_DIR, f"m4_3_timeline_baseline_{tag}.json"), "w") as f:
            json.dump(baseline_timeline, f, indent=2, default=str)
        with open(os.path.join(OUT_DIR, f"m4_3_timeline_selective_{tag}.json"), "w") as f:
            json.dump(selective_timeline, f, indent=2, default=str)
        print(f"Wrote timelines for {tag}")

    print("\nDone. Run app.eval.m4_3_analysis next to produce the aggregate tables.")


if __name__ == "__main__":
    main()
