"""M5.6: does the calibrated + tracked path ever CONFIRM a wrong value while
the camera is moving?

WHY THIS EXISTS. M5.6's browser arm (scripts/m5_6_browser_e2e.mjs --nudge)
was the first evidence in this project produced by a moving camera feeding
the real application through a real browser. It recorded, in SQLite,
`spo2 = 93.0 at confidence 86` on a monitor that displays 98 -- a
confidently-wrong confirmation (>= the 70 gate, wrong value). One
observation from one browser run is an anecdote, so this script turns it
into a measurement: replay a controlled camera nudge through the exact
production path and count how often it happens.

WHAT IT DOES NOT CLAIM. This is a SYNTHETIC motion stress test on a
SIMULATOR-rendered monitor. It is not a second real monitor and it is not a
real camera. Its value is that the motion is controlled and reproducible, so
the failure mode can be characterised rather than argued about. Real-camera
motion evidence remains the single dense Dataset B recording (M5.5 sec 18.2).

Production code is NOT modified by this script and NOT modified by M5.6 on
this path: app.pipeline.calibrated_roi, app.pipeline.layout_tracker,
app.pipeline.ocr and app.validation.reconcile are all byte-for-byte as M5.5
left them (M5.6 changed only app/api/pipeline.py, which this path never
touches).

Usage (from backend/):
    .venv/Scripts/python.exe app/eval/m5_6_motion_stress.py
"""

import json
import os
import sys
import time
from typing import Dict, List

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.getcwd())

from app.models.calibration import CalibrationProfile, NormalizedBox  # noqa: E402
from app.pipeline.calibrated_roi import (  # noqa: E402
    make_extractor, pad_roi_boxes, reference_pixel_boxes,
)
from app.pipeline.layout_tracker import LayoutTracker  # noqa: E402
from app.pipeline.read_frame import read_frame  # noqa: E402
from app.validation.reconcile import initial_confirmed_state, reconcile  # noqa: E402
from app.validation.rules import CONFIDENCE_MEDIUM_MIN  # noqa: E402

OUT_DIR = "app/eval/tier2_data/m5_6_report"
N_MOTION = 100

# Matching scripts/make_fake_camera_video.py's operator model exactly: the
# calibration UI tells the operator to draw the display SLOT, so the two
# fields that change digit count are drawn wider than today's digits.
SLOT_WIDEN = {"hr": 1.5, "spo2": 1.5}


def nudge(img: np.ndarray, t: float) -> np.ndarray:
    """The same pan+zoom+roll ramp the browser nudge video uses."""
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), 6.0 * t, 1.0 + 0.10 * t)
    M[0, 2] += 45.0 * t
    M[1, 2] += 30.0 * t
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(8, 10, 14))


def main() -> None:
    from simulator.render.monitor_layout import render_monitor

    os.makedirs(OUT_DIR, exist_ok=True)
    truth = {"hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 80, "nibpMean": 93,
             "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": int(time.time() * 1000)}
    ref_path = os.path.join(OUT_DIR, "_motion_reference.png")
    meta = render_monitor(truth, ref_path, layout="grid")
    ref = np.array(Image.open(ref_path).convert("RGB"))
    h, w = ref.shape[:2]

    boxes = {}
    for vital, (bx, by, bw, bh) in meta["rois"].items():
        bw = bw * SLOT_WIDEN.get(vital, 1.0)
        boxes[vital] = NormalizedBox(x=bx / w, y=by / h, w=bw / w, h=bh / h)
    padded = pad_roi_boxes(boxes)
    profile = CalibrationProfile(
        id="m5_6_motion", layout_id="default", reference_width=w, reference_height=h,
        roi_boxes=padded, field_meta={}, created_at=0, updated_at=0,
    )

    # EXACTLY how app.ws.vitals._camera_roi_extractor builds it. The
    # exclude_boxes argument is load-bearing, not decoration: it keeps the
    # tracker from anchoring on the digits themselves, which are the one part
    # of the scene that legitimately changes. An earlier version of this
    # script passed exclude_boxes=[] and therefore was not measuring the
    # production tracker at all.
    tracker = LayoutTracker.from_reference_image(
        ref, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )
    tracking_seen: List[dict] = []
    extractor = make_extractor(
        profile, tracker=tracker,
        on_tracking_result=lambda r: tracking_seen.append(
            {"ok": bool(r.ok), "status": r.status.value, "inliers": r.n_inliers,
             "scale": round(r.scale, 3), "rotation_deg": round(r.rotation_deg, 2)}
        ),
    )

    confirmed_state = initial_confirmed_state(truth["timestamp"])
    per_frame: List[dict] = []
    confidently_wrong: List[dict] = []

    scored = ("hr", "spo2", "etco2", "rr", "temp")
    for i in range(N_MOTION + 1):
        t = i / float(N_MOTION)
        frame = ref if i == 0 else nudge(ref, t)
        raw, conf = read_frame(frame, roi_extractor=extractor)
        reading, confirmed_state, flagged = reconcile(raw, conf, confirmed_state)

        track = tracking_seen[-1] if tracking_seen else None
        row = {"i": i, "t": round(t, 3), "tracking": track,
               "raw": {}, "confidence": {}, "confirmed": {}}
        for vital in scored:
            row["raw"][vital] = raw.get(vital)
            row["confidence"][vital] = conf.get(vital)
            row["confirmed"][vital] = reading.get(vital)
            # A CONFIRMED value that differs from what the monitor displays,
            # at or above the confidence gate, is the exact failure class this
            # project's safety posture exists to prevent.
            expected = truth[vital]
            got = reading.get(vital)
            if got is not None and abs(float(got) - float(expected)) > 1e-6:
                if (conf.get(vital) or 0) >= CONFIDENCE_MEDIUM_MIN:
                    confidently_wrong.append({
                        "frame": i, "t": round(t, 3), "vital": vital,
                        "displayed": expected, "confirmed": got,
                        "confidence": conf.get(vital),
                        "raw_read": raw.get(vital),
                        "tracking": track,
                    })
        per_frame.append(row)

    # Per-vital summary
    summary: Dict[str, dict] = {}
    for vital in scored:
        n_conf_wrong = sum(1 for c in confidently_wrong if c["vital"] == vital)
        raw_wrong = sum(1 for r in per_frame
                        if r["raw"][vital] is not None
                        and abs(float(r["raw"][vital]) - float(truth[vital])) > 1e-6)
        confs = [r["confidence"][vital] for r in per_frame if r["confidence"][vital] is not None]
        summary[vital] = {
            "frames": len(per_frame),
            "raw_misreads": raw_wrong,
            "confidently_wrong_confirmations": n_conf_wrong,
            "mean_confidence": round(sum(confs) / len(confs), 1) if confs else None,
        }

    print("=== M5.6 motion stress: controlled camera nudge, calibrated+tracked path ===")
    print(f"reference {w}x{h}, {N_MOTION} moving frames, "
          f"pan 45px / zoom 1.10x / roll 6deg ramp\n")
    print(f"{'vital':8s} {'frames':>7s} {'raw misreads':>13s} {'CONFIDENTLY WRONG':>19s} {'mean conf':>10s}")
    for vital, s in summary.items():
        print(f"{vital:8s} {s['frames']:>7d} {s['raw_misreads']:>13d} "
              f"{s['confidently_wrong_confirmations']:>19d} {str(s['mean_confidence']):>10s}")

    locked = sum(1 for r in per_frame if r["tracking"] and r["tracking"]["ok"])
    print(f"\ntracking lock rate: {locked}/{len(per_frame)} "
          f"({100.0 * locked / len(per_frame):.1f}%)")
    print(f"TOTAL confidently-wrong confirmations: {len(confidently_wrong)}")
    for c in confidently_wrong[:15]:
        print(f"  frame {c['frame']:>3d} (t={c['t']}) {c['vital']}: "
              f"displayed {c['displayed']} -> CONFIRMED {c['confirmed']} "
              f"at confidence {c['confidence']} (raw read {c['raw_read']})")
    if len(confidently_wrong) > 15:
        print(f"  ... and {len(confidently_wrong) - 15} more")

    out = {
        "reference": {"width": w, "height": h},
        "motion": {"frames": N_MOTION, "pan_px": 45, "zoom": 1.10, "roll_deg": 6.0},
        "truth": truth,
        "summary": summary,
        "confidently_wrong": confidently_wrong,
        "per_frame": per_frame,
    }
    path = os.path.join(OUT_DIR, "m5_6_motion_stress.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
