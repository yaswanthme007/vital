"""M5.4 Phase 1: measures whether candidate confirmation signals actually
predict OCR correctness on real data, BEFORE any production behaviour is
touched. Nothing here selects a production threshold from ground truth --
this script's job is to find out which signals separate correct from wrong
reads at all; docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md is where any
resulting production decision is justified, and any threshold it adopts is
re-verified against held-out data before shipping, never fit to maximise a
number in this file.

DATA SOURCES

  1. The M5.3 eval artifacts already on disk (app/eval/tier2_data/m5_3_report/
     *.json) -- per (frame, field) OCR confidence, correctness, ground truth
     and tracking status, produced by the REAL production calibrated+tracked
     ROI path (the "m5_3_tracked" arm) against frozen Dataset A (52 frames,
     zero camera motion), frozen Dataset B (17 frames, real motion,
     reference sample_0001), and the dense_B anchors (the same 17 moments at
     the recording's native 640x360). These already carry everything needed
     for confidence-bucket and chronological-agreement analysis -- reused
     as-is, not recomputed.

  2. A NEW temporal OCR run over the full 270-frame dense_B chronological
     sequence (200ms spacing, real camera motion across three framings).
     app.eval.m5_3_tracking_eval.run_dense_tracking_only() already proved
     the TRACKER holds lock at 97% here, but recorded no OCR values --
     "does the same value repeat across consecutive real frames" cannot be
     measured without actually reading them. This module extends that run
     with real per-frame OCR (production TesseractEngine, production
     calibrated_roi.make_extractor with a real LayoutTracker) so temporal
     agreement can be measured on data, not assumed.

Every measurement below is READ-ONLY against real datasets: it imports and
calls the unmodified app.pipeline / app.validation modules exactly as
production does, and writes only to app/eval/tier2_data/m5_4_report/. It
never trains anything, tunes a production constant, or writes into a
dataset or ground-truth file.

Usage:
    python -m app.eval.m5_4_signal_predictiveness
"""

import json
import os
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from app.models.calibration import CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import make_extractor, reference_pixel_boxes
from app.pipeline.layout_tracker import LayoutTracker, TrackingResult
from app.pipeline.ocr import NibpValue, TesseractEngine, _locate_tesseract_binary
from app.validation.rules import CONFIDENCE_MEDIUM_MIN, CONFIDENCE_HIGH_MIN

import pytesseract

_resolved = _locate_tesseract_binary(None)
if _resolved:
    pytesseract.pytesseract.tesseract_cmd = _resolved

ENGINE = TesseractEngine()

M53_DIR = "app/eval/tier2_data/m5_3_report"
DENSE_B_DIR = "app/eval/tier2_data/dense_B"
ANCHOR_DIR = "app/eval/tier2_data/dense_B_anchors"
ANCHOR_GT_PATH = os.path.join(ANCHOR_DIR, "m5_3_anchor_ground_truth_values.json")
OUT_DIR = "app/eval/tier2_data/m5_4_report"

CONFIDENCE_BUCKETS = [(0, 40), (40, 70), (70, 90), (90, 101)]


def _bucket(conf: float) -> str:
    for lo, hi in CONFIDENCE_BUCKETS:
        if lo <= conf < hi:
            return f"{lo}-{hi - 1}"
    return "?"


# ═══════════════════════════════════════════════════════════════════════
# Part A -- confidence-bucket predictiveness on the REAL production path
# (already-computed M5.3 tracked-arm records, no new inference).
# ═══════════════════════════════════════════════════════════════════════


def load_arm_records(json_path: str, arm: str = "m5_3_tracked") -> List[dict]:
    with open(json_path) as f:
        data = json.load(f)
    return [r for r in data["arms"][arm]["ocr_records"] if not r["excluded_from_scoring"]]


def confidence_bucket_table(records: List[dict], label: str) -> dict:
    """P(correct | confidence bucket), scored fields only, non-missing only
    (a missing read carries confidence 0 by construction and would otherwise
    just re-derive the trivial 'confidence 0 -> never correct' fact)."""
    present = [r for r in records if not r["missing"]]
    rows = {}
    for lo, hi in CONFIDENCE_BUCKETS:
        bucket = [r for r in present if lo <= r["confidence"] < hi]
        n = len(bucket)
        correct = sum(1 for r in bucket if r["correct"])
        rows[f"{lo}-{hi - 1}"] = {"n": n, "correct": correct, "p_correct": (correct / n) if n else None}
    return {"label": label, "n_present": len(present), "n_missing": len(records) - len(present), "buckets": rows}


# ═══════════════════════════════════════════════════════════════════════
# Part B -- chronological consecutive-agreement, on the sparse-but-real
# frozen datasets (no new inference -- reuses the same on-disk records).
# ═══════════════════════════════════════════════════════════════════════


def chronological_agreement(records: List[dict], label: str) -> dict:
    """Walks each field's chronologically-sorted (sample id order == real
    time order in both frozen datasets) sequence of raw OCR predictions.
    At each present (non-missing) reading, records the AGREEMENT RUN LENGTH
    ending there -- how many consecutive present readings, including this
    one, share the identical rounded value -- and whether that value is
    correct. This is the exact "same value N times running" question
    ARCHITECTURE.md's temporal-consistency section raised and EVIDENCE.md
    sec 8 flagged as unmeasurable on sparse stills; measured directly here
    rather than assumed either way."""
    by_field: Dict[str, List[dict]] = {}
    for r in records:
        by_field.setdefault(r["field"], []).append(r)
    for field_recs in by_field.values():
        field_recs.sort(key=lambda r: r["id"])

    run_stats: Dict[int, Dict[str, int]] = {}  # run_length -> {n, correct}
    agree_wrong_examples = []
    for field, field_recs in by_field.items():
        run_len = 0
        prev_val = None
        for r in field_recs:
            if r["missing"]:
                run_len = 0
                prev_val = None
                continue
            if prev_val is not None and r["predicted"] == prev_val:
                run_len += 1
            else:
                run_len = 1
            prev_val = r["predicted"]
            bucket = run_stats.setdefault(run_len, {"n": 0, "correct": 0})
            bucket["n"] += 1
            if r["correct"]:
                bucket["correct"] += 1
            elif run_len >= 2:
                agree_wrong_examples.append(
                    {"field": field, "id": r["id"], "run_length": run_len,
                     "value": r["predicted"], "ground_truth": r["ground_truth"]}
                )

    table = {
        str(k): {"n": v["n"], "correct": v["correct"], "p_correct": v["correct"] / v["n"] if v["n"] else None}
        for k, v in sorted(run_stats.items())
    }
    return {
        "label": label,
        "run_length_table": table,
        "agreeing_but_wrong_examples": agree_wrong_examples[:20],
        "n_agreeing_but_wrong_total": len(agree_wrong_examples),
    }


# ═══════════════════════════════════════════════════════════════════════
# Part C -- the dense_B temporal OCR run (new inference, real production
# code, 200ms real-motion recording). Extends
# m5_3_tracking_eval.run_dense_tracking_only with actual OCR reads.
# ═══════════════════════════════════════════════════════════════════════


def run_dense_temporal_ocr(reference_frame_id: str = "frame_000000") -> dict:
    with open(os.path.join(DENSE_B_DIR, "manifest.json")) as f:
        manifest = json.load(f)
    frames = manifest["frames"]

    ref_path = os.path.join(DENSE_B_DIR, reference_frame_id + ".png")
    ref_img = np.array(Image.open(ref_path).convert("RGB"))

    # Same geometry source m5_3_tracking_eval.run_dense_tracking_only uses:
    # anchor_004971 is the SAME moment as dense frame_000000 (both
    # video_frame_index 4971) -- confirmed by inspection of both files, not
    # assumed.
    with open(os.path.join(ANCHOR_DIR, "anchor_004971.json")) as f:
        anchor0 = json.load(f)
    h, w = ref_img.shape[:2]
    profile = CalibrationProfile(
        id="m5_4-dense", reference_width=w, reference_height=h,
        roi_boxes={v: NormalizedBox(x=b[0] / w, y=b[1] / h, w=b[2] / w, h=b[3] / h)
                   for v, b in anchor0["rois"].items()},
        created_at=0, updated_at=0,
    )
    tracker = LayoutTracker.from_reference_image(
        ref_img, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )
    sink: List[TrackingResult] = []
    extractor = make_extractor(profile, tracker=tracker, on_tracking_result=sink.append)

    vitals = sorted(profile.roi_boxes)  # hr, spo2, temp on this recording
    records = []
    t_start = time.perf_counter()
    for i, fr in enumerate(frames):
        img = np.array(Image.open(os.path.join(DENSE_B_DIR, fr["id"] + ".png")).convert("RGB"))
        before = len(sink)
        rois = extractor(img)
        tr = sink[-1] if len(sink) > before else None
        locked = tr.ok if tr is not None else (fr["id"] == reference_frame_id)

        per_vital = {}
        if fr["id"] == reference_frame_id:
            # The reference frame is what the tracker is built from; track()
            # is never called against itself. Still worth reading its own
            # OCR values as the sequence's frame 0.
            rois = make_extractor(profile)(ref_img)
            locked = True
        for vital in vitals:
            roi = rois.get(vital)
            if roi is None:
                per_vital[vital] = {"value": None, "confidence": 0.0}
                continue
            value, confidence = ENGINE.read_vital(roi.crop, vital)
            per_vital[vital] = {"value": value, "confidence": confidence}

        records.append({
            "index": i, "id": fr["id"], "timestamp_s": fr["timestamp_s"],
            "video_frame_index": fr["video_frame_index"],
            "locked": locked,
            "tracking_status": tr.status.value if tr is not None else ("ok" if locked else "n/a"),
            "n_inliers": tr.n_inliers if tr is not None else None,
            "per_vital": per_vital,
        })
        if (i + 1) % 50 == 0:
            print(f"  dense temporal OCR: {i + 1}/{len(frames)} frames "
                  f"({time.perf_counter() - t_start:.0f}s elapsed)")

    return {"reference_frame_id": reference_frame_id, "vitals": vitals, "n_frames": len(records), "records": records}


def _round_for_vital(vital: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 1) if vital == "temp" else round(value)


def dense_agreement_at_anchors(dense: dict, anchor_gt: dict) -> dict:
    """For each of the 17 real anchor moments (excluding the one used as the
    tracking reference), finds the matching dense-sequence frame by
    video_frame_index, then walks BACKWARD from it through the chronological,
    LOCKED-only OCR sequence to find the agreement run length ending there --
    exactly the state a live confirmation policy would have accumulated by
    that moment. Reports whether the repeated value matches ground truth,
    explicitly including the dangerous case (repeated but wrong)."""
    records = dense["records"]
    by_video_idx = {r["video_frame_index"]: i for i, r in enumerate(records)}
    vitals = [v for v in dense["vitals"] if v != "temp"]  # temp excluded from scoring, EVIDENCE.md sec 9

    anchor_files = sorted(f for f in os.listdir(ANCHOR_DIR) if f.startswith("anchor_") and f.endswith(".json"))
    rows = []
    for fname in anchor_files:
        with open(os.path.join(ANCHOR_DIR, fname)) as f:
            anchor = json.load(f)
        aid = anchor["id"]
        vidx = anchor["provenance"]["video_frame_index"]
        gt = anchor_gt.get(aid)
        if gt is None or vidx not in by_video_idx:
            continue
        anchor_pos = by_video_idx[vidx]

        for vital in vitals:
            if vital not in gt:
                continue
            gt_val = _round_for_vital(vital, gt[vital])

            # Walk backward from the anchor position through LOCKED frames,
            # accumulating the run of identical consecutive values -- the
            # value a live temporal-agreement policy would hold AT this
            # anchor moment, using only information available up to it.
            run_value = None
            run_len = 0
            run_confidences = []
            pos = anchor_pos
            while pos >= 0:
                rec = records[pos]
                if not rec["locked"]:
                    break
                pv = rec["per_vital"].get(vital)
                if pv is None or pv["value"] is None:
                    break
                v = _round_for_vital(vital, pv["value"])
                if run_value is None:
                    run_value = v
                if v != run_value:
                    break
                run_len += 1
                run_confidences.append(pv["confidence"])
                pos -= 1

            anchor_conf = records[anchor_pos]["per_vital"].get(vital, {}).get("confidence")
            anchor_val = _round_for_vital(vital, records[anchor_pos]["per_vital"].get(vital, {}).get("value"))
            rows.append({
                "anchor_id": aid, "vital": vital, "ground_truth": gt_val,
                "anchor_frame_value": anchor_val, "anchor_frame_confidence": anchor_conf,
                "anchor_frame_correct": anchor_val == gt_val if anchor_val is not None else None,
                "run_length": run_len, "run_value": run_value,
                "run_mean_confidence": (sum(run_confidences) / len(run_confidences)) if run_confidences else None,
                "run_value_correct": (run_value == gt_val) if run_value is not None else None,
            })

    # Aggregate: does a longer agreement run predict correctness better than
    # the single anchor-frame OCR read alone?
    def agg(min_run: int) -> dict:
        subset = [r for r in rows if r["run_length"] >= min_run and r["run_value"] is not None]
        n = len(subset)
        correct = sum(1 for r in subset if r["run_value_correct"])
        return {"n": n, "correct": correct, "p_correct": correct / n if n else None}

    single_frame = [r for r in rows if r["anchor_frame_value"] is not None]
    single_frame_correct = sum(1 for r in single_frame if r["anchor_frame_correct"])

    dangerous = [r for r in rows if r["run_length"] >= 2 and r["run_value_correct"] is False]

    return {
        "n_anchor_vital_pairs": len(rows),
        "single_frame_baseline": {
            "n": len(single_frame), "correct": single_frame_correct,
            "p_correct": single_frame_correct / len(single_frame) if single_frame else None,
        },
        "agreement_run_ge_1": agg(1),
        "agreement_run_ge_2": agg(2),
        "agreement_run_ge_3": agg(3),
        "agreement_run_ge_5": agg(5),
        "dangerous_repeated_but_wrong": dangerous,
        "n_dangerous_repeated_but_wrong": len(dangerous),
        "rows": rows,
    }


# ═══════════════════════════════════════════════════════════════════════
# Part D -- combined signal: confidence bucket x agreement, on whatever
# already-scored records carry both (frozen A/B chronological + dense
# anchors), to see whether corroboration helps specifically WITHIN the
# low-confidence population the confidence gate currently holds.
# ═══════════════════════════════════════════════════════════════════════


def combined_confidence_and_agreement(chrono_result: dict, records: List[dict]) -> dict:
    """Re-derives, from the SAME per-field records chronological_agreement()
    already walked, the joint distribution of (confidence bucket, agreement
    run length) -> P(correct), restricted to the sub-CONFIDENCE_MEDIUM_MIN
    population -- the only population a new signal could possibly help,
    since anything already clearing the gate needs no help."""
    by_field: Dict[str, List[dict]] = {}
    for r in records:
        by_field.setdefault(r["field"], []).append(r)
    for field_recs in by_field.values():
        field_recs.sort(key=lambda r: r["id"])

    low_conf_rows = []
    for field, field_recs in by_field.items():
        run_len = 0
        prev_val = None
        for r in field_recs:
            if r["missing"]:
                run_len = 0
                prev_val = None
                continue
            if prev_val is not None and r["predicted"] == prev_val:
                run_len += 1
            else:
                run_len = 1
            prev_val = r["predicted"]
            if r["confidence"] < CONFIDENCE_MEDIUM_MIN:
                low_conf_rows.append({"field": field, "id": r["id"], "run_length": run_len,
                                       "confidence": r["confidence"], "correct": r["correct"]})

    def agg(min_run: int) -> dict:
        subset = [r for r in low_conf_rows if r["run_length"] >= min_run]
        n = len(subset)
        correct = sum(1 for r in subset if r["correct"])
        return {"n": n, "correct": correct, "p_correct": correct / n if n else None}

    return {
        "note": "restricted to confidence < CONFIDENCE_MEDIUM_MIN (70) -- the population reconcile() currently holds",
        "all_low_confidence": agg(0),
        "low_confidence_run_ge_2": agg(2),
        "low_confidence_run_ge_3": agg(3),
    }


# ═══════════════════════════════════════════════════════════════════════
# Driver
# ═══════════════════════════════════════════════════════════════════════


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    report: dict = {}

    print("=== Part A: confidence-bucket predictiveness (real m5_3_tracked path) ===")
    datasets = {
        "frozen_A": os.path.join(M53_DIR, "m5_3_frozen_A.json"),
        "frozen_B[sample_0001]": os.path.join(M53_DIR, "m5_3_frozen_B_sample_0001.json"),
        "dense_B_anchors": os.path.join(M53_DIR, "m5_3_dense_B_anchors.json"),
    }
    all_records: Dict[str, List[dict]] = {}
    conf_tables = {}
    for label, path in datasets.items():
        recs = load_arm_records(path)
        all_records[label] = recs
        table = confidence_bucket_table(recs, label)
        conf_tables[label] = table
        print(f"  {label}: n_present={table['n_present']}")
        for bucket, stats in table["buckets"].items():
            print(f"    conf {bucket:>7s}: n={stats['n']:4d}  P(correct)={stats['p_correct']}")
    report["confidence_bucket_tables"] = conf_tables

    print("\n=== Part B: chronological consecutive-agreement (frozen A/B) ===")
    agreement_tables = {}
    for label, recs in all_records.items():
        result = chronological_agreement(recs, label)
        agreement_tables[label] = result
        print(f"  {label}: run-length -> P(correct)")
        for run_len, stats in result["run_length_table"].items():
            print(f"    run>={run_len}: n={stats['n']:4d}  P(correct)={stats['p_correct']}")
        print(f"    agreeing-but-WRONG occurrences: {result['n_agreeing_but_wrong_total']}")
    report["chronological_agreement"] = agreement_tables

    print("\n=== Part C: dense_B temporal OCR run (270 frames, real motion) ===")
    dense = run_dense_temporal_ocr()
    with open(os.path.join(OUT_DIR, "m5_4_dense_temporal_ocr.json"), "w") as f:
        json.dump(dense, f, indent=2, default=str)
    n_locked = sum(1 for r in dense["records"] if r["locked"])
    print(f"  {dense['n_frames']} frames, {n_locked} locked ({n_locked / dense['n_frames'] * 100:.1f}%)")

    with open(ANCHOR_GT_PATH) as f:
        anchor_gt = json.load(f)["values"]
    dense_agreement = dense_agreement_at_anchors(dense, anchor_gt)
    report["dense_temporal_agreement"] = dense_agreement
    print(f"\n  anchor-vital pairs scored: {dense_agreement['n_anchor_vital_pairs']}")
    print(f"  single-frame baseline:      {dense_agreement['single_frame_baseline']}")
    print(f"  agreement run >=1:          {dense_agreement['agreement_run_ge_1']}")
    print(f"  agreement run >=2:          {dense_agreement['agreement_run_ge_2']}")
    print(f"  agreement run >=3:          {dense_agreement['agreement_run_ge_3']}")
    print(f"  agreement run >=5:          {dense_agreement['agreement_run_ge_5']}")
    print(f"  DANGEROUS (repeated but wrong): {dense_agreement['n_dangerous_repeated_but_wrong']}")
    for d in dense_agreement["dangerous_repeated_but_wrong"]:
        print(f"    {d}")

    print("\n=== Part D: confidence x agreement, restricted to sub-gate population ===")
    combined_tables = {}
    for label, recs in all_records.items():
        chrono = agreement_tables[label]
        combined = combined_confidence_and_agreement(chrono, recs)
        combined_tables[label] = combined
        print(f"  {label}: {combined}")
    report["combined_low_confidence_agreement"] = combined_tables

    with open(os.path.join(OUT_DIR, "m5_4_signal_predictiveness.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nWrote reports to {OUT_DIR}/")


if __name__ == "__main__":
    main()
