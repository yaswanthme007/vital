"""M5.3 Phase 6: does the tracker refuse what it should refuse?

The accuracy harness (app.eval.m5_3_tracking_eval) measures what tracking
does when it works. This measures what it does when it MUST NOT work --
because under this project's safety posture a tracker that silently returns a
confident wrong transform is far more dangerous than one that returns
nothing. Every case below is deliberately constructed so the correct answer
is known in advance, and each records the full statistic set so the
thresholds in app.pipeline.layout_tracker can be re-derived from evidence
rather than re-guessed.

THREE OUTCOME CLASSES, and the distinction matters:

  MUST_REJECT   the frame does not show the calibrated monitor at all, or the
                recovered geometry is pathological. Returning a transform here
                would crop a vital from whatever happens to be at those
                coordinates -- the confidently-wrong failure mode.

  MUST_TRACK    the frame DOES show the calibrated monitor, unmoved, merely
                degraded. Geometry is not the failing stage here and the
                tracker must not pretend otherwise; legibility is OCR
                confidence's job, and reconcile()'s gate already holds a value
                whose crop is unreadable. A tracker that rejected these would
                be withholding readings for a problem it is not qualified to
                judge.

  MUST_RECOVER  a known synthetic transform inside the accepted bounds. Checks
                the tracker returns the RIGHT geometry, not merely some
                geometry -- measured as recovered-vs-applied error.

  SAFE_EITHER_WAY  the monitor has not moved but its CONTENT has been
                destroyed (blacked out, blurred past legibility). Geometry is
                genuinely unchanged, so "identity" is not a wrong answer, and
                a tracker refusing for lack of features is not a wrong answer
                either -- the tracker is not qualified to judge legibility.
                What must NOT happen is a confident wrong READING, so these
                cases additionally run the real OCR over the resulting crops
                and assert nothing clears reconcile()'s confidence gate. That
                assertion is checked here rather than assumed, because it is
                the entire basis for letting the tracker return OK on a frame
                whose pixels are useless.

SYNTHETIC WARPS APPEAR ONLY IN THIS FILE, and only as a rejection/recovery
probe with a known answer. They are never presented as temporal evidence and
never enter an accuracy claim -- real camera motion is measured on the real
recording in m5_3_tracking_eval.

Usage:
    python -m app.eval.m5_3_negative_controls
"""

import json
import os
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from app.models.calibration import CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import make_extractor
from app.pipeline.layout_tracker import (
    MAX_ROTATION_DEG,
    MAX_SCALE,
    MIN_INLIERS,
    MIN_RAW_MATCHES,
    MIN_SCALE,
    LayoutTracker,
    TrackingStatus,
)
from app.pipeline.ocr import TesseractEngine, _locate_tesseract_binary
from app.validation.rules import CONFIDENCE_MEDIUM_MIN

import pytesseract

_resolved = _locate_tesseract_binary(None)
if _resolved:
    pytesseract.pytesseract.tesseract_cmd = _resolved

_ENGINE = TesseractEngine()

FROZEN_B = "app/eval/tier2_data/external_monitor_B"
FROZEN_A = "app/eval/tier2_data/external_monitor_video"
DENSE_B = "app/eval/tier2_data/dense_B"
OUT_DIR = "app/eval/tier2_data/m5_3_report"

MUST_REJECT = "MUST_REJECT"
MUST_TRACK = "MUST_TRACK"
MUST_RECOVER = "MUST_RECOVER"
SAFE_EITHER_WAY = "SAFE_EITHER_WAY"


def _load(path: str) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"))


def _warp(img: np.ndarray, scale: float, rotation_deg: float, tx: float, ty: float) -> np.ndarray:
    """Applies a KNOWN similarity so the correct recovered transform is known
    in advance."""
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), rotation_deg, scale)
    M[0, 2] += tx
    M[1, 2] += ty
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))


def _blur(img: np.ndarray, fraction_of_width: float = 0.022) -> np.ndarray:
    """Blur radius scaled to the image, so the same case is equally severe at
    2712x1220 and at 640x360. A fixed pixel kernel is NOT comparable across
    resolutions -- 61px is a mild smear on the former and total destruction on
    the latter, which made an early version of this file report a spurious
    failure."""
    k = max(3, int(img.shape[1] * fraction_of_width) | 1)
    return cv2.GaussianBlur(img, (k, k), 0)


def _occlude(img: np.ndarray, fraction: float) -> np.ndarray:
    out = img.copy()
    h, w = out.shape[:2]
    out[:, : int(w * fraction)] = 0
    return out


def build_cases(reference: np.ndarray) -> List[dict]:
    h, w = reference.shape[:2]
    rng = np.random.default_rng(20260819)
    cases: List[dict] = [
        {"name": "different monitor (Dataset A frame)", "expect": MUST_REJECT,
         "why": "a completely different device -- any transform would crop a vital from unrelated pixels",
         "image": _load(os.path.join(FROZEN_A, "sample_0006.png"))},
        {"name": "pure noise", "expect": MUST_REJECT,
         "why": "no scene at all",
         "image": (rng.random((h, w, 3)) * 255).astype(np.uint8)},
        {"name": "black frame (lens capped)", "expect": MUST_REJECT,
         "why": "no features exist to anchor to",
         "image": np.zeros((h, w, 3), np.uint8)},
        {"name": "uniform grey frame", "expect": MUST_REJECT,
         "why": "featureless, the degenerate case a detector can silently mishandle",
         "image": np.full((h, w, 3), 128, np.uint8)},
        {"name": "90% occluded", "expect": SAFE_EITHER_WAY,
         "why": "the sliver still visible genuinely has not moved, so identity is not a WRONG "
                "transform -- but the crops land on black. Geometry cannot detect blackness; the "
                "OCR confidence gate must, and is asserted below",
         "image": _occlude(reference, 0.9)},
        {"name": "extreme scale 4.7x", "expect": MUST_REJECT,
         "why": "the brief's own pathological example -- outside MIN_SCALE/MAX_SCALE",
         "image": _warp(reference, 4.7, 0.0, 0.0, 0.0)},
        {"name": "extreme rotation 81deg", "expect": MUST_REJECT,
         "why": "the brief's own pathological example -- outside MAX_ROTATION_DEG",
         "image": _warp(reference, 1.0, 81.0, 0.0, 0.0)},
        {"name": "vertical flip", "expect": MUST_REJECT,
         "why": "a similarity cannot express a reflection; must not be fitted as one",
         "image": reference[::-1].copy()},

        {"name": "heavy blur (smeared lens)", "expect": SAFE_EITHER_WAY,
         "why": "the monitor has NOT moved, so identity is correct -- but past a point the blur "
                "leaves no features at all and refusing is equally correct. Either way no "
                "confident reading may result, which is asserted below",
         "image": _blur(reference)},
        {"name": "darkened 60% (lights down)", "expect": MUST_TRACK,
         "why": "same geometry, less light",
         "image": (reference * 0.4).astype(np.uint8)},
        {"name": "30% occluded (hand in shot)", "expect": MUST_TRACK,
         "why": "most of the monitor chrome is still visible; the layout has not moved",
         "image": _occlude(reference, 0.3)},
        {"name": "JPEG-degraded", "expect": MUST_TRACK,
         "why": "compression artifacts must not be mistaken for movement",
         "image": cv2.imdecode(cv2.imencode(".jpg", reference, [int(cv2.IMWRITE_JPEG_QUALITY), 25])[1], 1)},

        {"name": "known warp: scale 1.25", "expect": MUST_RECOVER, "truth": (1.25, 0.0),
         "image": _warp(reference, 1.25, 0.0, 0.0, 0.0)},
        {"name": "known warp: scale 0.75", "expect": MUST_RECOVER, "truth": (0.75, 0.0),
         "image": _warp(reference, 0.75, 0.0, 0.0, 0.0)},
        {"name": "known warp: rotation 8deg", "expect": MUST_RECOVER, "truth": (1.0, 8.0),
         "image": _warp(reference, 1.0, 8.0, 0.0, 0.0)},
        {"name": "known warp: scale 1.4 + rot -5deg", "expect": MUST_RECOVER, "truth": (1.4, -5.0),
         "image": _warp(reference, 1.4, -5.0, 0.0, 0.0)},
    ]
    return cases


def _profile_for(
    reference_path: str, reference: np.ndarray, annotation_path: Optional[str] = None
) -> Optional[CalibrationProfile]:
    """The calibration profile that goes with this reference frame. Used only
    to obtain real crops for the downstream OCR assertion below -- no
    ground-truth box ever reaches a tracking decision.

    annotation_path is explicit for the dense frames, whose annotations live
    in dense_B_anchors/ rather than beside the image. Without it the profile
    would silently be None and the SAFE_EITHER_WAY assertion would be skipped
    while still reporting PASS -- a skipped check must never look like a
    passed one, so run() now fails loudly instead."""
    boxes_json = annotation_path or reference_path.replace(".png", ".json")
    if not os.path.exists(boxes_json):
        return None
    with open(boxes_json) as f:
        rois = json.load(f).get("rois", {})
    if not rois:
        return None
    h, w = reference.shape[:2]
    return CalibrationProfile(
        id="negative-controls", reference_width=w, reference_height=h,
        roi_boxes={v: NormalizedBox(x=b[0] / w, y=b[1] / h, w=b[2] / w, h=b[3] / h)
                   for v, b in rois.items()},
        created_at=0, updated_at=0,
    )


def _worst_confidence(profile: CalibrationProfile, tracker: LayoutTracker, frame: np.ndarray) -> dict:
    """Runs the REAL production OCR over the tracked crops of a
    content-destroyed frame and reports the highest confidence any field
    achieved. The safety claim being checked is that this stays below
    reconcile()'s gate, i.e. a useless frame cannot produce a confirmed value
    even when the tracker legitimately reports a lock."""
    extractor = make_extractor(profile, tracker=tracker)
    rois = extractor(frame)
    per_field = {}
    worst = 0.0
    for vital, roi in rois.items():
        if roi is None:
            per_field[vital] = None
            continue
        value, confidence = _ENGINE.read_vital(roi.crop, vital)
        per_field[vital] = {"value": str(value) if value is not None else None,
                            "confidence": round(confidence, 1)}
        worst = max(worst, confidence)
    return {"max_confidence": worst, "gate": CONFIDENCE_MEDIUM_MIN, "per_field": per_field}


def run(reference_path: str, label: str, annotation_path: Optional[str] = None) -> dict:
    reference = _load(reference_path)
    boxes_json = annotation_path or reference_path.replace(".png", ".json")
    exclude = None
    if os.path.exists(boxes_json):
        with open(boxes_json) as f:
            exclude = [tuple(float(c) for c in b) for b in json.load(f).get("rois", {}).values()]
    tracker = LayoutTracker.from_reference_image(reference, exclude_boxes=exclude)

    profile = _profile_for(reference_path, reference, annotation_path)

    rows = []
    passed = 0
    for case in build_cases(reference):
        r = tracker.track(case["image"])
        expect = case["expect"]
        downstream = None

        if expect == MUST_REJECT:
            correct = not r.ok
        elif expect == MUST_TRACK:
            correct = r.ok
        elif expect == SAFE_EITHER_WAY:
            # Geometry may legitimately go either way here. What is NOT
            # optional is that no CONFIDENT reading survives -- so when the
            # tracker does return a transform, read the resulting crops with
            # the real production OCR and require every confidence to sit
            # below reconcile()'s gate.
            correct = True
            if r.ok:
                if profile is None:
                    raise RuntimeError(
                        f"{case['name']}: tracker returned OK but no calibration profile is "
                        f"available to verify the downstream OCR gate. Refusing to report this "
                        f"as a pass on an unverified safety claim."
                    )
                downstream = _worst_confidence(profile, tracker, case["image"])
                correct = downstream["max_confidence"] < CONFIDENCE_MEDIUM_MIN
        else:
            true_scale, true_rot = case["truth"]
            # cv2.getRotationMatrix2D rotates counter-clockwise for positive
            # angles while atan2(M[1,0], M[0,0]) reads clockwise-positive, so
            # the recovered rotation is the negation of the applied one.
            correct = (
                r.ok
                and abs(r.scale - true_scale) < 0.05
                and abs(abs(r.rotation_deg) - abs(true_rot)) < 2.0
            )
        passed += bool(correct)
        row = r.to_dict()
        row.update({"case": case["name"], "expect": expect, "why": case.get("why", ""),
                    "handled_correctly": bool(correct)})
        if downstream is not None:
            row["downstream_ocr"] = downstream
        if "truth" in case:
            row["applied_scale"], row["applied_rotation_deg"] = case["truth"]
        rows.append(row)
        mark = "PASS" if correct else "**FAIL**"
        detail = f"scale={r.scale:.3f} rot={r.rotation_deg:7.2f}" if r.status != TrackingStatus.NO_FRAME_FEATURES else ""
        if downstream is not None:
            detail += f"  max_ocr_conf={downstream['max_confidence']:.1f} (gate {CONFIDENCE_MEDIUM_MIN})"
        print(f"  {mark:8s} {case['name']:34s} -> {r.status.value:22s} "
              f"matches={r.n_matches:5d} inliers={r.n_inliers:5d} {detail}")

    return {"reference": label, "reference_path": reference_path,
            "thresholds": {"MIN_RAW_MATCHES": MIN_RAW_MATCHES, "MIN_INLIERS": MIN_INLIERS,
                           "MIN_SCALE": MIN_SCALE, "MAX_SCALE": MAX_SCALE,
                           "MAX_ROTATION_DEG": MAX_ROTATION_DEG},
            "n_cases": len(rows), "n_handled_correctly": passed, "cases": rows}


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    results = []
    for path, label, annotation in (
        (os.path.join(FROZEN_B, "sample_0001.png"), "frozen_B/sample_0001 (2712x1220)", None),
        # The dense frames' annotations live in dense_B_anchors/, not beside
        # the image, so this must be passed explicitly -- otherwise both the
        # digit-masking and the downstream OCR assertion would be skipped.
        (os.path.join(DENSE_B, "frame_000000.png"), "dense_B/frame_000000 (640x360)",
         os.path.join("app/eval/tier2_data/dense_B_anchors", "anchor_004971.json")),
    ):
        print(f"\n=== negative controls against {label} ===")
        results.append(run(path, label, annotation))

    dest = os.path.join(OUT_DIR, "m5_3_negative_controls.json")
    with open(dest, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print("\n=== summary ===")
    for r in results:
        print(f"  {r['reference']}: {r['n_handled_correctly']}/{r['n_cases']} handled correctly")
    print(f"\nWrote {dest}")


if __name__ == "__main__":
    main()
