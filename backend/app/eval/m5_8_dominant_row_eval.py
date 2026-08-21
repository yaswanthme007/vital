"""M5.8: measures the DOMINANT-ROW reader against the reader it replaces,
on every body of real evidence this repo has.

WHY A NEW EVAL AND A NEW DATASET. Every prior OCR milestone measured
against Dataset A (a downloaded monitor video) and Dataset B (17
photographed stills), both annotated with boxes drawn TIGHTLY around the
digits. Production's actual input is neither: it is an ROI an operator
drags around a field's display SLOT on a live 720p webcam feed, which
necessarily also contains that field's alarm-limit labels because the
monitor draws them inside the slot. That difference is the entire root
cause M5.8 addresses, and no existing dataset exercises it.

The evidence this script adds is the demo laptop's own database: every
CalibrationProfile ever saved on it stores the exact frame the operator
drew on (`calibration_reference_frames`) alongside the exact boxes they
drew (`calibration_profiles.roi_boxes`). That is a real photograph of a
real physical anaesthesia monitor, cropped by a real operator's real
boxes, run through the real production padding -- the closest thing to
"what the camera actually sees on stage" that can be replayed offline.

GROUND TRUTH for those frames is a per-frame JSON transcribed by a human
reading the monitor's digits off the full-size image (same methodology as
Dataset A/B's own ground truth: no OCR was used to produce any value).
See --write-gt-template to regenerate the skeleton for a new capture set.

Usage (from backend/):
    .venv/Scripts/python.exe -m app.eval.m5_8_dominant_row_eval
    .venv/Scripts/python.exe -m app.eval.m5_8_dominant_row_eval --sweep-pad
"""

import argparse
import io
import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from app.models.calibration import NormalizedBox
from app.pipeline.calibrated_roi import extract_rois_from_boxes, pad_roi_boxes
from app.pipeline.ocr import NibpValue, TesseractEngine
from app.validation.crop_integrity import crop_is_suspicious
from app.validation.rules import normalize_temp_celsius

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "..", "vital.db")
GT_PATH = os.path.join(os.path.dirname(__file__), "tier2_data", "real_camera", "ground_truth.json")
OUT_DIR = os.path.join(os.path.dirname(__file__), "tier2_data", "m5_8_report")


def _load_profiles(db_path: str, wanted: List[str]) -> Dict[str, Tuple[dict, bytes]]:
    """(roi_boxes, reference frame bytes) per profile id, straight out of the
    live database -- no copy, no re-encode, no re-annotation."""
    out: Dict[str, Tuple[dict, bytes]] = {}
    conn = sqlite3.connect(db_path)
    try:
        for profile_id in wanted:
            row = conn.execute(
                "SELECT p.roi_boxes, f.image_bytes FROM calibration_profiles p "
                "JOIN calibration_reference_frames f ON f.profile_id = p.id WHERE p.id = ?",
                (profile_id,),
            ).fetchone()
            if row is not None:
                out[profile_id] = (json.loads(row[0]), row[1])
    finally:
        conn.close()
    return out


def _normalize(vital: str, value: object) -> Optional[object]:
    """The engine's value in the same shape the ground truth records it."""
    if value is None:
        return None
    if vital == "nibp":
        if isinstance(value, NibpValue) and value.systolic is not None and value.diastolic is not None:
            return f"{value.systolic:g}/{value.diastolic:g}"
        return None
    if vital == "temp":
        # Ground truth records Celsius; this monitor displays Fahrenheit and
        # rules.normalize_temp_celsius is what production converts with.
        return round(normalize_temp_celsius(float(value)), 1)
    return float(value)


def run(db_path: str, gt_path: str) -> dict:
    with open(gt_path) as f:
        ground_truth = json.load(f)
    profiles = _load_profiles(db_path, sorted(ground_truth))
    engine = TesseractEngine()

    per_vital = {v: {"correct": 0, "wrong": 0, "noread": 0} for v in VITALS}
    integrity = {"correct_clean": 0, "correct_flagged": 0, "wrong_clean": 0, "wrong_flagged": 0}
    records: List[dict] = []

    for profile_id, expected in sorted(ground_truth.items()):
        if profile_id not in profiles:
            continue
        boxes, image_bytes = profiles[profile_id]
        img = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        # The SAME padding save_profile applies, so these are production's
        # boxes, not the operator's raw drag.
        padded = pad_roi_boxes({v: NormalizedBox(**b) for v, b in boxes.items()})
        rois = extract_rois_from_boxes(img, padded)

        for vital in VITALS:
            roi = rois.get(vital)
            truth = expected.get(vital)
            if roi is None or truth is None:
                continue
            value, confidence, diag = engine.read_vital_with_diagnostics(roi.crop, vital)
            got = _normalize(vital, value)
            suspicious = crop_is_suspicious(diag)

            if got is None:
                verdict = "noread"
            elif got == truth:
                verdict = "correct"
            else:
                verdict = "wrong"
            per_vital[vital][verdict] += 1
            if verdict != "noread":
                integrity[f"{verdict}_{'flagged' if suspicious else 'clean'}"] += 1

            records.append({
                "profileId": profile_id, "vital": vital, "expected": truth, "got": got,
                "verdict": verdict, "confidence": round(confidence, 1),
                "rawText": diag.raw_text, "matchedText": diag.matched_text,
                "incompleteRow": diag.incomplete_row, "cropSuspicious": suspicious,
            })

    totals = {k: sum(per_vital[v][k] for v in VITALS) for k in ("correct", "wrong", "noread")}
    scored = sum(totals.values()) or 1
    return {
        "framesScored": len(profiles),
        "fieldsScored": scored,
        "perVital": per_vital,
        "totals": totals,
        "correctRate": round(100 * totals["correct"] / scored, 1),
        "wrongRate": round(100 * totals["wrong"] / scored, 1),
        "noreadRate": round(100 * totals["noread"] / scored, 1),
        # How well crop integrity separates the wrong reads from the right
        # ones. A wrong-but-CLEAN read is the dangerous residue: nothing
        # downstream can tell it apart from a genuine one.
        "cropIntegrity": integrity,
        "records": records,
    }


def write_gt_template(db_path: str, out_path: str) -> None:
    """Skeleton for a fresh capture set: every profile that has a stored
    reference frame, with null values for a human to fill in BY READING THE
    MONITOR IMAGE. Deliberately does not pre-fill anything from OCR."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT p.id FROM calibration_profiles p "
            "JOIN calibration_reference_frames f ON f.profile_id = p.id ORDER BY p.created_at"
        ).fetchall()
    finally:
        conn.close()
    template = {r[0]: {v: None for v in VITALS} for r in rows}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(template, f, indent=1)
    print(f"wrote {out_path} -- {len(template)} frames awaiting human transcription")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--gt", default=GT_PATH)
    parser.add_argument("--write-gt-template", action="store_true")
    args = parser.parse_args()

    if args.write_gt_template:
        write_gt_template(args.db, args.gt)
        return

    if not os.path.isfile(args.gt):
        raise SystemExit(
            f"No ground truth at {args.gt}. Run with --write-gt-template and transcribe the values by eye."
        )

    result = run(args.db, args.gt)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "m5_8_real_camera_eval.json"), "w") as f:
        json.dump(result, f, indent=1)

    print(f"real camera frames: {result['framesScored']}, fields scored: {result['fieldsScored']}")
    for vital in VITALS:
        s = result["perVital"][vital]
        print(f"  {vital:>6}: correct={s['correct']:>3} wrong={s['wrong']:>3} noread={s['noread']:>3}")
    print(f"  TOTAL : correct {result['correctRate']}%  WRONG {result['wrongRate']}%  noread {result['noreadRate']}%")
    print(f"  crop integrity: {result['cropIntegrity']}")
    print(f"  records -> {os.path.join(OUT_DIR, 'm5_8_real_camera_eval.json')}")


if __name__ == "__main__":
    main()
