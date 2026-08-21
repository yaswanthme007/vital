"""M5.3 Phase 7/8: does per-frame layout tracking actually make VITAL safer?

This is the script docs/EVIDENCE.md sec 10 reserved the name of. It extends
app.eval.m5_2_calibration_eval's discipline rather than replacing it --
localization, OCR and reconcile() are measured as THREE SEPARATE signals and
never collapsed into one accuracy number, and the M5.2 aggregation helpers are
imported and reused unmodified so the two milestones' numbers are computed by
the same code.

THE COMPARISON THAT MATTERS. Every arm below runs on IDENTICAL frames, from
an IDENTICAL calibration profile, against IDENTICAL ground truth:

    m5_2_static           the calibrated box, never moved (M5.2's shipped behaviour)
    m5_3_tracked          the calibrated box re-anchored per frame, FAIL-CLOSED
    m5_3_static_fallback  DIAGNOSTIC ONLY -- on tracking failure, fall back to
                          the static box instead of withholding. Exists purely to
                          put a number on what fail-closed costs and buys; it is
                          not a shipping mode.

A NOTE ON THE M5.2 BASELINE REPRODUCED HERE. m5_2_calibration_eval builds its
profile from the EARLIEST frame that happens to annotate each vital, so
different vitals can come from different frames. That is fine without tracking
(a static box has no reference frame to be consistent with) but is incoherent
WITH tracking, which re-anchors everything through one reference image -- a
Phase 0 probe that mixed per-vital calibration frames drove RR's tracked IoU
to 0.000. So every arm here calibrates all boxes from ONE frame. Consequently
the m5_2_static numbers here are NOT identical to the M5.2 report's published
numbers, and are not meant to be: they are the correct like-for-like baseline
for this experiment. The M5.2 report's own figures stand unchanged.

DATASETS. See docs/M5_3_LAYOUT_TRACKING_REPORT.md for full provenance.
  frozen_B  17 real frames, 2712x1220, REAL framing changes. Primary accuracy evidence.
  frozen_A  52 real frames, 2712x1220, ZERO measured camera motion. The no-op
            control: a tracker must not invent motion where there is none.
  dense_B_anchors  the same 17 moments taken from the ORIGINAL recording at its
            native 640x360, ground truth carried across by a verified similarity
            (app.eval.m5_3_dense_annotate).
  dense_B   270 chronological frames at 200ms spacing from the original
            recording -- the temporal evidence the frozen stills cannot provide.
            No per-frame value GT exists, so this arm reports TRACKING metrics
            only (lock rate, stability, latency), never an accuracy claim.

Usage:
    python -m app.eval.m5_3_tracking_eval
"""

import json
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from app.eval.m5_2_calibration_eval import (
    FIELD_DECIMALS,
    FIELD_TO_VITAL,
    FIELDS,
    VITALS,
    _round_display,
    aggregate_localization,
    aggregate_ocr,
    run_reconcile_replay,
)
from app.eval.tier2_common import iou
from app.models.calibration import CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import make_extractor, reference_pixel_boxes
from app.pipeline.layout_tracker import LayoutTracker, TrackingResult, TrackingStatus
from app.pipeline.ocr import NibpValue, TesseractEngine, _locate_tesseract_binary

import pytesseract

_resolved = _locate_tesseract_binary(None)
if _resolved:
    pytesseract.pytesseract.tesseract_cmd = _resolved

ENGINE = TesseractEngine()
OUT_DIR = "app/eval/tier2_data/m5_3_report"

FROZEN_A_DIR = "app/eval/tier2_data/external_monitor_video"
FROZEN_A_GT = os.path.join(FROZEN_A_DIR, "m4_ocr_report", "m4_ground_truth_values.json")
FROZEN_B_DIR = "app/eval/tier2_data/external_monitor_B"
FROZEN_B_GT = os.path.join(FROZEN_B_DIR, "m5_ground_truth_values.json")
ANCHOR_DIR = "app/eval/tier2_data/dense_B_anchors"
ANCHOR_GT = os.path.join(ANCHOR_DIR, "m5_3_anchor_ground_truth_values.json")
DENSE_B_DIR = "app/eval/tier2_data/dense_B"

# Same exclusions, same reasons, as M5.1 and M5.2 (EVIDENCE.md sec 9):
# Dataset B's Temp GT box is clipped and its value is outside RANGE_BOUNDS
# regardless of any localization or OCR quality. Carried forward verbatim so
# this milestone scores the same population its predecessors did.
DATASET_B_EXCLUDE = {"temp"}


# --- dataset loading ------------------------------------------------------


def _load_samples(directory: str, prefix: str) -> List[dict]:
    """Chronologically ordered (id-sorted) frames with their annotations."""
    out = []
    for name in sorted(os.listdir(directory)):
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        stem = name[: -len(".json")]
        png = os.path.join(directory, stem + ".png")
        if not os.path.exists(png):
            continue
        with open(os.path.join(directory, name)) as f:
            label = json.load(f)
        out.append({"id": stem, "png_path": png, "label": label})
    return out


def _gt_box(label: dict, vital: str):
    raw = label.get("rois", {}).get(vital)
    if not raw or raw[2] <= 0 or raw[3] <= 0:
        return None
    return tuple(float(c) for c in raw)


def _gt_values(gt_all: dict, sample_id: str) -> Dict[str, Optional[float]]:
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


def build_single_frame_profile(sample: dict) -> Tuple[CalibrationProfile, np.ndarray]:
    """One reference frame, ALL boxes from it -- the operator's single
    calibration pass. Returns the profile and the reference image itself,
    because tracking needs the actual pixels, not just the geometry."""
    img = np.array(Image.open(sample["png_path"]).convert("RGB"))
    h, w = img.shape[:2]
    boxes = {}
    for vital in VITALS:
        b = _gt_box(sample["label"], vital)
        if b is None:
            continue
        boxes[vital] = NormalizedBox(x=b[0] / w, y=b[1] / h, w=b[2] / w, h=b[3] / h)
    profile = CalibrationProfile(
        id=f"m5_3-{sample['id']}", reference_width=w, reference_height=h,
        roi_boxes=boxes, created_at=0, updated_at=0,
    )
    return profile, img


# --- arms -----------------------------------------------------------------


def _make_arm(
    arm: str, profile: CalibrationProfile, reference_img: np.ndarray, sink: List[TrackingResult]
) -> Callable[[np.ndarray], dict]:
    if arm == "m5_2_static":
        return make_extractor(profile)

    tracker = LayoutTracker.from_reference_image(
        reference_img, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )
    tracked = make_extractor(profile, tracker=tracker, on_tracking_result=sink.append)
    if arm == "m5_3_tracked":
        return tracked

    if arm == "m5_3_static_fallback":
        static = make_extractor(profile)

        def _fallback(img: np.ndarray):
            result = tracked(img)
            if all(v is None for v in result.values()):
                return static(img)   # DIAGNOSTIC ONLY -- see module docstring
            return result

        return _fallback

    raise ValueError(f"unknown arm {arm}")


ARMS = ("m5_2_static", "m5_3_tracked", "m5_3_static_fallback")


# --- the measurement ------------------------------------------------------


def run_arm(
    dataset: str, arm: str, samples: List[dict], gt_all: dict, reference_id: str, exclude: set
) -> dict:
    ref_sample = next(s for s in samples if s["id"] == reference_id)
    profile, ref_img = build_single_frame_profile(ref_sample)

    tracking_sink: List[TrackingResult] = []
    extractor = _make_arm(arm, profile, ref_img, tracking_sink)

    localization: List[dict] = []
    ocr_records: List[dict] = []
    tracking_records: List[dict] = []
    ref_boxes_px = reference_pixel_boxes(profile)

    for s in samples:
        if s["id"] == reference_id:
            continue  # never score a frame against the profile drawn on it
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        h, w = img.shape[:2]
        values = _gt_values(gt_all, s["id"])

        before = len(tracking_sink)
        t0 = time.perf_counter()
        rois = extractor(img)
        extract_ms = (time.perf_counter() - t0) * 1000.0

        tr = tracking_sink[-1] if len(tracking_sink) > before else None
        if tr is not None:
            rec = tr.to_dict()
            rec.update({"dataset": dataset, "arm": arm, "id": s["id"], "extractMs": round(extract_ms, 2)})
            tracking_records.append(rec)

        for vital in VITALS:
            roi = rois.get(vital)

            gt_b = _gt_box(s["label"], vital)
            if gt_b is not None and vital in profile.roi_boxes:
                if roi is not None:
                    pred = (float(roi.box[0]), float(roi.box[1]), float(roi.box[2]), float(roi.box[3]))
                else:
                    # Withheld. Scored as IoU 0 rather than dropped: silently
                    # excluding the frames an arm refused would flatter
                    # whichever arm refuses most, which is exactly the kind of
                    # selective reporting this milestone's brief forbids.
                    pred = None
                localization.append({
                    "dataset": dataset, "arm": arm, "id": s["id"], "vital": vital,
                    "iou": iou(pred, gt_b) if pred is not None else 0.0,
                    "withheld": pred is None,
                })

            group = ["nibpSystolic", "nibpDiastolic", "nibpMean"] if vital == "nibp" else [vital]
            if all(values[f] is None for f in group):
                continue

            if roi is None:
                parsed = {f: None for f in group}
                confidence, ocr_ms = 0.0, 0.0
            else:
                t1 = time.perf_counter()
                value, confidence = ENGINE.read_vital(roi.crop, vital)
                ocr_ms = (time.perf_counter() - t1) * 1000.0
                if vital == "nibp":
                    parsed = ({"nibpSystolic": value.systolic, "nibpDiastolic": value.diastolic,
                               "nibpMean": value.mean} if isinstance(value, NibpValue)
                              else {f: None for f in group})
                else:
                    parsed = {vital: value}

            for fld in group:
                gt = values[fld]
                if gt is None:
                    continue
                dec = FIELD_DECIMALS[fld]
                pred_v = _round_display(parsed[fld], dec)
                excluded = fld in exclude or FIELD_TO_VITAL[fld] in exclude
                ocr_records.append({
                    "dataset": dataset, "arm": arm, "id": s["id"], "vital": vital, "field": fld,
                    "ground_truth": _round_display(gt, dec), "predicted": pred_v,
                    "missing": parsed[fld] is None,
                    "correct": None if excluded else (parsed[fld] is not None and pred_v == _round_display(gt, dec)),
                    "excluded_from_scoring": excluded,
                    "confidence": confidence,
                    "withheld_by_tracking": roi is None,
                    "ocr_latency_s": ocr_ms / 1000.0,
                    "extract_latency_s": extract_ms / 1000.0,
                })

    return {
        "dataset": dataset, "arm": arm, "reference_id": reference_id,
        "calibrated_vitals": sorted(profile.roi_boxes),
        "reference_boxes_px": {v: [round(c, 1) for c in b] for v, b in ref_boxes_px.items()},
        "n_eval_frames": len(samples) - 1,
        "localization_records": localization,
        "ocr_records": ocr_records,
        "tracking_records": tracking_records,
    }


# --- aggregation ----------------------------------------------------------


def aggregate_tracking(records: List[dict]) -> dict:
    if not records:
        return {"n": 0, "note": "no tracking performed (static arm)"}
    hist: Dict[str, int] = {}
    for r in records:
        hist[r["status"]] = hist.get(r["status"], 0) + 1
    ok = [r for r in records if r["ok"]]

    def stat(key, rows):
        vals = [r[key] for r in rows]
        return {"min": min(vals), "max": max(vals), "mean": sum(vals) / len(vals)} if vals else None

    return {
        "n": len(records),
        "lock_rate": len(ok) / len(records),
        "status_histogram": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
        "inliers_on_lock": stat("nInliers", ok),
        "inlier_ratio_on_lock": stat("inlierRatio", ok),
        "reprojection_on_lock": stat("meanReprojectionError", ok),
        "scale_on_lock": stat("scale", ok),
        "rotation_on_lock": stat("rotationDeg", ok),
        "translation_on_lock": stat("translationPx", ok),
        "track_latency_ms": stat("elapsedMs", records),
        "extract_latency_ms": stat("extractMs", records),
    }


def paired_failure_analysis(static_recs: List[dict], tracked_recs: List[dict]) -> dict:
    """Phase 9's "do not blame the wrong stage". On identical (frame, field)
    pairs, splits outcomes into what tracking CAUSED versus what it merely
    failed to fix."""
    key = lambda r: (r["id"], r["field"])
    s_by = {key(r): r for r in static_recs if not r["excluded_from_scoring"]}
    t_by = {key(r): r for r in tracked_recs if not r["excluded_from_scoring"]}
    shared = sorted(set(s_by) & set(t_by))

    out = {
        "n_paired": len(shared),
        "fixed_by_tracking": 0,        # static wrong -> tracked right
        "broken_by_tracking": 0,       # static right -> tracked wrong
        "wrong_in_both": 0,            # tracking-independent failure
        "right_in_both": 0,
        "withheld_by_tracking_was_right_statically": 0,
        "withheld_by_tracking_was_wrong_statically": 0,
        "examples_broken": [],
        "examples_fixed": [],
    }
    for k in shared:
        s, t = s_by[k], t_by[k]
        if t["withheld_by_tracking"]:
            if s["correct"]:
                out["withheld_by_tracking_was_right_statically"] += 1
            else:
                out["withheld_by_tracking_was_wrong_statically"] += 1
        if s["correct"] and t["correct"]:
            out["right_in_both"] += 1
        elif (not s["correct"]) and t["correct"]:
            out["fixed_by_tracking"] += 1
            if len(out["examples_fixed"]) < 12:
                out["examples_fixed"].append({"id": k[0], "field": k[1],
                                              "static": s["predicted"], "tracked": t["predicted"],
                                              "gt": t["ground_truth"]})
        elif s["correct"] and not t["correct"]:
            out["broken_by_tracking"] += 1
            if len(out["examples_broken"]) < 12:
                out["examples_broken"].append({"id": k[0], "field": k[1],
                                               "static": s["predicted"], "tracked": t["predicted"],
                                               "gt": t["ground_truth"],
                                               "withheld": t["withheld_by_tracking"]})
        else:
            out["wrong_in_both"] += 1
    return out


def run_dense_tracking_only(reference_id: str = "frame_000000") -> dict:
    """The temporal arm. No value ground truth exists for the full dense
    sequence, so this reports ONLY what needs no ground truth: whether the
    tracker holds lock across a continuous recording with real camera motion,
    how its transform behaves frame to frame, and what it costs. Making an
    accuracy claim here would require inventing labels, so none is made."""
    with open(os.path.join(DENSE_B_DIR, "manifest.json")) as f:
        manifest = json.load(f)
    frames = manifest["frames"]

    ref_path = os.path.join(DENSE_B_DIR, reference_id + ".png")
    ref_img = np.array(Image.open(ref_path).convert("RGB"))

    # The dense reference is the same moment as frozen sample_0001, so reuse
    # that human calibration, mapped into this resolution via the anchor.
    anchor_json = os.path.join(ANCHOR_DIR, "anchor_004971.json")
    with open(anchor_json) as f:
        anchor = json.load(f)
    h, w = ref_img.shape[:2]
    profile = CalibrationProfile(
        id="m5_3-dense", reference_width=w, reference_height=h,
        roi_boxes={v: NormalizedBox(x=b[0] / w, y=b[1] / h, w=b[2] / w, h=b[3] / h)
                   for v, b in anchor["rois"].items()},
        created_at=0, updated_at=0,
    )
    tracker = LayoutTracker.from_reference_image(
        ref_img, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )
    sink: List[TrackingResult] = []
    extractor = make_extractor(profile, tracker=tracker, on_tracking_result=sink.append)

    records = []
    prev_ok = None
    for fr in frames:
        if fr["id"] == reference_id:
            continue
        img = np.array(Image.open(os.path.join(DENSE_B_DIR, fr["id"] + ".png")).convert("RGB"))
        before = len(sink)
        t0 = time.perf_counter()
        extractor(img)
        extract_ms = (time.perf_counter() - t0) * 1000.0
        if len(sink) == before:
            continue
        tr = sink[-1]
        rec = tr.to_dict()
        rec.update({"id": fr["id"], "timestampS": fr["timestamp_s"], "extractMs": round(extract_ms, 2)})
        # Frame-to-frame transform stability: how far this frame's estimate
        # moved from the previous LOCKED estimate. Recorded, not gated -- a
        # bound needs more evidence than one recording provides.
        if tr.ok and prev_ok is not None:
            rec["scaleStepFromPrevLock"] = round(abs(tr.scale - prev_ok.scale), 4)
            rec["rotationStepFromPrevLock"] = round(abs(tr.rotation_deg - prev_ok.rotation_deg), 3)
            rec["translationStepFromPrevLock"] = round(abs(tr.translation_px - prev_ok.translation_px), 2)
        if tr.ok:
            prev_ok = tr
        records.append(rec)

    agg = aggregate_tracking(records)
    steps = [r["translationStepFromPrevLock"] for r in records if "translationStepFromPrevLock" in r]
    rot_steps = [r["rotationStepFromPrevLock"] for r in records if "rotationStepFromPrevLock" in r]
    agg["stability"] = {
        "n_consecutive_locked_pairs": len(steps),
        "translation_step_px": {"mean": sum(steps) / len(steps), "max": max(steps)} if steps else None,
        "rotation_step_deg": {"mean": sum(rot_steps) / len(rot_steps), "max": max(rot_steps)} if rot_steps else None,
    }
    return {"dataset": "dense_B", "reference_id": reference_id, "n_frames": len(records),
            "aggregate": agg, "records": records}


# --- driver ---------------------------------------------------------------


def evaluate(dataset: str, directory: str, prefix: str, gt_path: str, reference_id: str, exclude: set) -> dict:
    with open(gt_path) as f:
        gt_all = json.load(f)["values"]
    samples = _load_samples(directory, prefix)
    print(f"\n[{dataset}] {len(samples)} frames, reference={reference_id}")

    arms: Dict[str, dict] = {}
    for arm in ARMS:
        t0 = time.perf_counter()
        r = run_arm(dataset, arm, samples, gt_all, reference_id, exclude)
        r["localization"] = aggregate_localization(r["localization_records"])
        r["ocr"] = aggregate_ocr(r["ocr_records"])
        r["tracking"] = aggregate_tracking(r["tracking_records"])
        r["reconcile"] = run_reconcile_replay(r["ocr_records"], f"{dataset}/{arm}")
        arms[arm] = r
        loc, ocr, rec = r["localization"]["_overall"], r["ocr"], r["reconcile"]
        print(f"  {arm:22s} IoU={loc['mean_iou']:.3f} r@.3={loc['recall_at_0.3'] * 100:5.1f}% "
              f"OCR={(ocr['accuracy'] or 0) * 100:5.1f}% conf_acc={(rec['micro_confirmed_accuracy'] or 0) * 100:5.1f}% "
              f"CW={rec['confidently_wrong_confirmations']:2d} lock={r['tracking'].get('lock_rate', float('nan'))}"
              f" [{time.perf_counter() - t0:.0f}s]")

    arms["_paired_failure_analysis"] = paired_failure_analysis(
        arms["m5_2_static"]["ocr_records"], arms["m5_3_tracked"]["ocr_records"]
    )
    return {"dataset": dataset, "reference_id": reference_id, "arms": arms}


def _delta_table(result: dict) -> List[str]:
    d, a = result["dataset"], result["arms"]
    s, t = a["m5_2_static"], a["m5_3_tracked"]
    rows = [
        ("mean IoU", s["localization"]["_overall"]["mean_iou"], t["localization"]["_overall"]["mean_iou"], 3),
        ("IoU recall@0.3", s["localization"]["_overall"]["recall_at_0.3"], t["localization"]["_overall"]["recall_at_0.3"], 3),
        ("IoU recall@0.5", s["localization"]["_overall"]["recall_at_0.5"], t["localization"]["_overall"]["recall_at_0.5"], 3),
        ("OCR accuracy", s["ocr"]["accuracy"], t["ocr"]["accuracy"], 3),
        ("OCR missing rate", s["ocr"]["missing_rate"], t["ocr"]["missing_rate"], 3),
        ("confirmed accuracy", s["reconcile"]["micro_confirmed_accuracy"], t["reconcile"]["micro_confirmed_accuracy"], 3),
        ("confidently-wrong", s["reconcile"]["confidently_wrong_confirmations"],
         t["reconcile"]["confidently_wrong_confirmations"], 0),
    ]
    out = [f"--- {d}: M5.2 static vs M5.3 tracked (reference {result['reference_id']}) ---",
           f"{'metric':24s}{'M5.2':>10s}{'M5.3':>10s}{'delta':>10s}"]
    for name, sv, tv, dp in rows:
        sv = sv if sv is not None else 0
        tv = tv if tv is not None else 0
        fmt = f"{{:>10.{dp}f}}" if dp else "{:>10.0f}"
        out.append(f"{name:24s}" + fmt.format(sv) + fmt.format(tv) + fmt.format(tv - sv))
    # per-vital IoU, mandatory
    out.append(f"\n{'per-vital mean IoU':24s}{'M5.2':>10s}{'M5.3':>10s}{'delta':>10s}")
    for vital in VITALS:
        sv = s["localization"].get(vital)
        tv = t["localization"].get(vital)
        if not sv or not tv or not sv.get("n"):
            continue
        out.append(f"  {vital:22s}{sv['mean_iou']:>10.3f}{tv['mean_iou']:>10.3f}{tv['mean_iou'] - sv['mean_iou']:>10.3f}")
    out.append(f"\n{'per-vital OCR accuracy':24s}{'M5.2':>10s}{'M5.3':>10s}{'delta':>10s}")
    for vital in VITALS:
        sv = s["ocr"]["per_vital"].get(vital)
        tv = t["ocr"]["per_vital"].get(vital)
        if not sv or not sv.get("n"):
            continue
        out.append(f"  {vital:22s}{(sv['accuracy'] or 0):>10.3f}{(tv['accuracy'] or 0):>10.3f}"
                   f"{((tv['accuracy'] or 0) - (sv['accuracy'] or 0)):>10.3f}")
    pf = a["_paired_failure_analysis"]
    out.append(f"\npaired failure analysis (n={pf['n_paired']}): fixed_by_tracking={pf['fixed_by_tracking']} "
               f"broken_by_tracking={pf['broken_by_tracking']} wrong_in_both={pf['wrong_in_both']} "
               f"right_in_both={pf['right_in_both']}")
    out.append(f"  withheld by tracking that static got RIGHT: {pf['withheld_by_tracking_was_right_statically']}"
               f"   that static got WRONG: {pf['withheld_by_tracking_was_wrong_statically']}")
    fb = a["m5_3_static_fallback"]["reconcile"]
    out.append(f"  [diagnostic] static-fallback arm: confirmed_acc="
               f"{(fb['micro_confirmed_accuracy'] or 0) * 100:.1f}%  confidently_wrong={fb['confidently_wrong_confirmations']}")
    return out


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    results = []

    # Frozen Dataset B -- two reference choices, both reported. Reference
    # choice is a real operator decision with a large measured effect, and
    # picking only the flattering one would misrepresent the method.
    for ref in ("sample_0001", "sample_0011"):
        results.append(evaluate(f"frozen_B[{ref}]", FROZEN_B_DIR, "sample_", FROZEN_B_GT, ref, DATASET_B_EXCLUDE))

    # Frozen Dataset A -- the no-op control. sample_0006 is the only frame
    # annotating all six vitals.
    results.append(evaluate("frozen_A", FROZEN_A_DIR, "sample_", FROZEN_A_GT, "sample_0006", set()))

    # Dense anchors -- same moments, original recording resolution.
    results.append(evaluate("dense_B_anchors", ANCHOR_DIR, "anchor_", ANCHOR_GT, "anchor_004971", DATASET_B_EXCLUDE))

    print("\n[dense_B] temporal arm (tracking metrics only, no accuracy claim) ...")
    dense = run_dense_tracking_only()
    agg = dense["aggregate"]
    print(f"  frames={dense['n_frames']}  lock_rate={agg['lock_rate'] * 100:.1f}%  "
          f"statuses={agg['status_histogram']}")

    for r in results:
        with open(os.path.join(OUT_DIR, f"m5_3_{r['dataset'].replace('[', '_').replace(']', '')}.json"), "w") as f:
            json.dump(r, f, indent=2, default=str)
    with open(os.path.join(OUT_DIR, "m5_3_dense_B_tracking.json"), "w") as f:
        json.dump(dense, f, indent=2, default=str)

    lines: List[str] = ["=== M5.3 layout tracking: M5.2 static vs M5.3 tracked, identical frames ===", ""]
    for r in results:
        lines.extend(_delta_table(r))
        lines.append("")
    lines.append("--- dense_B temporal arm (tracking only; no value ground truth exists per frame) ---")
    lines.append(f"frames={dense['n_frames']}  lock_rate={agg['lock_rate'] * 100:.1f}%")
    lines.append(f"status histogram: {agg['status_histogram']}")
    lines.append(f"latency: track {agg['track_latency_ms']}")
    lines.append(f"stability: {agg['stability']}")

    text = "\n".join(lines)
    with open(os.path.join(OUT_DIR, "m5_3_summary.txt"), "w") as f:
        f.write(text + "\n")
    print("\n" + text)
    print(f"\nWrote reports to {OUT_DIR}/")


if __name__ == "__main__":
    main()
