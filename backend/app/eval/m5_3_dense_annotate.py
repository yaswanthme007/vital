"""M5.3 Stage 1b: give the dense sequence ground truth WITHOUT inventing any.

THE PROBLEM. app.eval.m5_3_dense_extract produced a dense, chronological
frame sequence from the original Dataset B recording, but a bare video frame
carries no ROI boxes and no transcribed values -- and localization IoU / OCR
accuracy / reconcile() replay all need them.

THE SOLUTION, AND WHY IT IS NOT FABRICATION. Every one of the 17 frozen
Dataset B samples was located inside this very video at a specific frame
index, each with 688-1211 RANSAC inliers (m5_3_data_provenance.json) -- i.e.
a frozen sample and its source video frame are the SAME picture at a
different resolution, not two similar pictures. So the existing HUMAN
annotation can be carried onto the video frame by the verified similarity
transform between them. Nothing new is invented: the boxes are still the ones
a person drew, and the values are still the ones a person transcribed
(m5_ground_truth_values.json); only the coordinate system changes.

WHAT THIS DELIBERATELY AVOIDS.
  - The transform is estimated between a frozen screenshot and ITS OWN source
    frame -- a near-identity re-sampling at ~700-1200 inliers. It is NOT the
    tracker being evaluated, is not estimated between two different moments
    in time, and is never used to choose or score a tracking transform.
    Feeding the evaluated tracker's own output back in as ground truth would
    be circular; this is not that.
  - Anchors use the EXACT video frame each sample was matched to, never a
    nearby frame, so a transcribed value is never attributed to a moment the
    person did not actually read. (Values can change within 0.1s; boxes drift
    with the camera. Neither is approximated here.)
  - Every anchor records its own inlier count and transform, so a weak mapping
    is visible in the artifacts rather than silently averaged in.

OUTPUT. app/eval/tier2_data/dense_B_anchors/ -- the exact anchor frames plus
sample_-style JSON (same `rois` convention app.eval.harness.load_dataset
already reads), a values file mirroring m5_ground_truth_values.json's shape,
and a provenance record per anchor.

Usage:
    python -m app.eval.m5_3_dense_annotate
"""

import json
import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from app.eval.m5_3_dense_extract import (
    MATCH_MIN_INLIERS,
    SOURCES,
    _match_inliers,
    _orb,
    _to_work_gray,
    video_metadata,
)

FROZEN_DIR = "app/eval/tier2_data/external_monitor_B"
FROZEN_VALUES = os.path.join(FROZEN_DIR, "m5_ground_truth_values.json")
PROVENANCE = "app/eval/tier2_data/m5_3_report/m5_3_data_provenance.json"
ANCHOR_DIR = "app/eval/tier2_data/dense_B_anchors"

# An anchor is only usable if its frozen sample maps onto its source video
# frame far more convincingly than the Phase 0 negative controls ever did
# (those topped out at 11 inliers). The observed correspondence run produced
# 688-1211, so this bar rejects nothing real -- it exists so that a future
# re-run against a different/re-encoded video cannot silently degrade the
# ground truth without failing loudly.
ANCHOR_MIN_INLIERS = max(MATCH_MIN_INLIERS, 200)


def _transform_box(box: Tuple[float, float, float, float], M: np.ndarray) -> Tuple[float, float, float, float]:
    """Maps an (x, y, w, h) box through a 2x3 affine and returns the
    axis-aligned bounding box of the result -- the same convention the
    tracker and every IoU consumer in this codebase use."""
    x, y, w, h = box
    corners = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]).reshape(-1, 1, 2)
    t = cv2.transform(corners, M).reshape(-1, 2)
    nx, ny = float(t[:, 0].min()), float(t[:, 1].min())
    return nx, ny, float(t[:, 0].max() - nx), float(t[:, 1].max() - ny)


def build_anchors() -> dict:
    with open(PROVENANCE) as f:
        prov = json.load(f)
    b = prov.get("B")
    if not b or b.get("gate") != "PASSED":
        raise RuntimeError("Dataset B correspondence gate has not passed -- run m5_3_dense_extract first")

    video_path = b["video"]["path"]
    meta = video_metadata(video_path)
    if meta["sha256"] != b["video"]["sha256"]:
        raise RuntimeError("Source video sha256 no longer matches the recorded provenance")

    with open(FROZEN_VALUES) as f:
        frozen_values = json.load(f)["values"]

    os.makedirs(ANCHOR_DIR, exist_ok=True)
    orb = _orb()
    cap = cv2.VideoCapture(video_path)

    anchors: List[dict] = []
    values_out: Dict[str, dict] = {}

    for entry in b["correspondence"]["per_sample"]:
        sid = entry["sample_id"]
        idx = entry["video_frame_index"]
        if idx is None:
            continue

        frozen_png = os.path.join(FROZEN_DIR, sid + ".png")
        frozen_json = os.path.join(FROZEN_DIR, sid + ".json")
        with open(frozen_json) as f:
            label = json.load(f)
        rois = label.get("rois", {})
        if not rois:
            continue

        # The frozen sample, at the working resolution the transform is
        # estimated in, plus the scale that took it there -- needed to express
        # the mapping in ORIGINAL frozen-pixel coordinates, which is what the
        # human-drawn boxes are in.
        frozen_full = np.array(Image.open(frozen_png).convert("RGB"))
        frozen_gray, s_frozen = _to_work_gray(frozen_full)
        kp_s, desc_s = orb.detectAndCompute(frozen_gray, None)

        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, bgr = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        video_gray, s_video = _to_work_gray(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        kp_v, desc_v = orb.detectAndCompute(video_gray, None)

        n_inl, M_work = _match_inliers(desc_s, kp_s, desc_v, kp_v)
        if M_work is None or n_inl < ANCHOR_MIN_INLIERS:
            anchors.append({"sample_id": sid, "video_frame_index": idx, "inliers": n_inl,
                            "usable": False, "reason": "insufficient inliers for a trustworthy GT mapping"})
            print(f"  {sid}: inliers={n_inl} -- REJECTED as an anchor")
            continue

        # M_work maps frozen-working-px -> video-working-px. Compose the two
        # scale changes so the result maps ORIGINAL frozen px -> ORIGINAL
        # video px, which is the space the human boxes and the dense frames
        # actually live in.
        M = M_work.copy()
        M[:, :2] *= (s_frozen / s_video)
        M[:, 2] /= s_video

        anchor_id = f"anchor_{idx:06d}"
        Image.fromarray(rgb).save(os.path.join(ANCHOR_DIR, anchor_id + ".png"))

        mapped: Dict[str, List[float]] = {}
        for vital, box in rois.items():
            nx, ny, nw, nh = _transform_box(tuple(float(c) for c in box), M)
            # Clip to the frame; a box the camera moved partly out of is
            # recorded as what remains visible, never extrapolated past the edge.
            x0, y0 = max(0.0, nx), max(0.0, ny)
            x1 = min(float(rgb.shape[1]), nx + nw)
            y1 = min(float(rgb.shape[0]), ny + nh)
            if x1 - x0 <= 1 or y1 - y0 <= 1:
                continue
            mapped[vital] = [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]

        with open(os.path.join(ANCHOR_DIR, anchor_id + ".json"), "w") as f:
            json.dump({
                "id": anchor_id,
                "rois": mapped,
                "conditions": label.get("conditions", []),
                "notes": label.get("notes", ""),
                "provenance": {
                    "derived_from_frozen_sample": sid,
                    "video_frame_index": idx,
                    "video_timestamp_s": entry["video_timestamp_s"],
                    "mapping_inliers": n_inl,
                    "mapping": "human-drawn boxes carried through a verified similarity transform "
                               "between the frozen screenshot and its own source video frame; "
                               "NOT produced by the tracker under evaluation",
                },
            }, f, indent=2)

        if sid in frozen_values:
            values_out[anchor_id] = frozen_values[sid]

        anchors.append({"sample_id": sid, "anchor_id": anchor_id, "video_frame_index": idx,
                        "video_timestamp_s": entry["video_timestamp_s"], "inliers": n_inl,
                        "usable": True, "n_rois": len(mapped)})
        print(f"  {sid} -> {anchor_id}  inliers={n_inl:5d}  rois={sorted(mapped)}")

    cap.release()

    with open(os.path.join(ANCHOR_DIR, "m5_3_anchor_ground_truth_values.json"), "w") as f:
        json.dump({
            "note": "Values are the UNCHANGED human transcriptions from "
                    "external_monitor_B/m5_ground_truth_values.json, re-keyed to the anchor id of "
                    "the exact source video frame each frozen sample was matched to.",
            "values": values_out,
        }, f, indent=2)

    manifest = {
        "dataset": "dense_B_anchors",
        "purpose": "ground-truth-bearing frames of the dense Dataset B recording, for localization "
                   "IoU / OCR / reconcile scoring",
        "holdout_note": "A DIFFERENT SPLIT OF THE SAME RECORDING as the frozen 17-frame Dataset B "
                        "-- not an independent monitor.",
        "anchor_min_inliers": ANCHOR_MIN_INLIERS,
        "n_usable": sum(1 for a in anchors if a.get("usable")),
        "anchors": anchors,
    }
    with open(os.path.join(ANCHOR_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main() -> None:
    print("Mapping frozen Dataset B ground truth onto its own source video frames ...")
    m = build_anchors()
    print(f"\n{m['n_usable']}/{len(m['anchors'])} anchors usable -> {ANCHOR_DIR}")


if __name__ == "__main__":
    main()
