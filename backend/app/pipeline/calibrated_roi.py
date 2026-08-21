"""M5.2: the calibrated ROI stage. Same contract as
app.pipeline.roi.extract_rois_by_colour / app.pipeline.tier2_roi.
extract_rois_by_field_classifier -- takes an RGB image, returns
Dict[str, Optional[VitalRoiResult]], one entry per located vital. That
shared contract is what makes this a drop-in swap inside read_frame()
(its `roi_extractor` param) and inside app.sources.camera.CameraSource (its
own `roi_extractor` param) -- nothing downstream (OCR, reconcile, alerts,
persistence, WebSocket) needs to know or care that a crop came from an
operator-drawn box instead of colour segmentation or the FieldCNN.

This reproduces exactly the "calibrated box, no tracking" mode measured in
docs/EVIDENCE.md sec 6 (Dataset B: 5.7% -> 35% OCR accuracy, mean box IoU
0.54) -- the SAME fixed, operator-drawn box is reused on every later frame,
with NO per-frame homography/keypoint re-estimation. That per-frame
re-anchoring (ORB keypoints against the monitor's static chrome) is
`docs/EVIDENCE.md`'s higher "+tracking" row (57% OCR accuracy, IoU 0.68) and
is explicitly `docs/ROADMAP.md`'s M5.3, not this milestone -- see
docs/M5_2_REAL_CALIBRATION_REPORT.md sec 3/19 for the evidence-based reason
this file does not attempt it.

A CalibrationProfile's boxes are stored NORMALIZED (fraction of the
calibration reference frame's width/height), so the same profile maps onto
a later frame of a different resolution (or a benignly different aspect
ratio, e.g. a slightly cropped getUserMedia stream) via a plain rescale --
Phase 6's resolution-independence requirement -- without needing any
geometric correction this milestone hasn't measured.
"""

import logging
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from app.models.calibration import CalibrationProfile, NormalizedBox
from app.pipeline.layout_tracker import (
    LayoutTracker,
    TrackingResult,
    TrackingStatus,
    check_transformed_rois,
    transform_box,
)
from app.pipeline.types import VitalRoiResult

logger = logging.getLogger(__name__)

# A resized/re-aspected live frame is expected and must keep working (Phase
# 6). A frame whose aspect ratio has drifted THIS far from the calibration
# reference is treated as probably a different camera framing/setup
# entirely -- not a benign resize -- so the caller withholds every field
# rather than silently mapping boxes onto a frame calibration says nothing
# reliable about (Phase 12's fail-safe posture: "Reading unavailable" beats
# a guessed region). This is a deliberately loose, undocumented-elsewhere
# heuristic, not a value tuned against measured data -- see
# docs/M5_2_REAL_CALIBRATION_REPORT.md sec 8 (Failure modes) for why a loose
# bound was chosen over a hard, precisely-derived one: there is no dataset
# of "same camera, deliberately different aspect ratio" to tune it against.
MAX_ASPECT_RATIO_DRIFT = 0.20


def aspect_ratio_drift(reference_width: int, reference_height: int, frame_w: int, frame_h: int) -> float:
    """Relative change in width:height between the calibration reference
    frame and a live frame. 0.0 == identical aspect ratio."""
    ref_ratio = reference_width / reference_height
    frame_ratio = frame_w / frame_h
    return abs(frame_ratio - ref_ratio) / ref_ratio


def _box_to_pixels(box: NormalizedBox, frame_w: int, frame_h: int) -> Optional[Tuple[int, int, int, int]]:
    x0, y0 = max(0, int(round(box.x * frame_w))), max(0, int(round(box.y * frame_h)))
    x1 = min(frame_w, int(round((box.x + box.w) * frame_w)))
    y1 = min(frame_h, int(round((box.y + box.h) * frame_h)))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def extract_rois_from_boxes(
    img: np.ndarray, roi_boxes: Dict[str, NormalizedBox]
) -> Dict[str, Optional[VitalRoiResult]]:
    """The actual box -> crop mapping: normalized box -> this frame's pixel
    box -> crop. No geometric correction beyond a plain width/height
    rescale -- see module docstring for why that's the deliberate M5.2
    scope boundary, not an oversight. Used directly by the calibration
    Verify step (app.api.calibration) against an UNSAVED candidate box set,
    and indirectly (via make_extractor below) by the live calibrated path."""
    h, w = img.shape[:2]
    results: Dict[str, Optional[VitalRoiResult]] = {}
    for vital, box in roi_boxes.items():
        clipped = _box_to_pixels(box, w, h)
        if clipped is None:
            results[vital] = None
            continue
        x, y, bw, bh = clipped
        crop = img[y : y + bh, x : x + bw]
        if crop.size == 0:
            results[vital] = None
            continue
        results[vital] = VitalRoiResult(box=(x, y, bw, bh), crop=crop.copy(), source_colour=None, engine="calibrated")
    return results


# Automatic width-only safety margin applied to every box at save time
# (app.api.calibration.save_profile), NOT something the operator has to
# remember to do. Directly evidence-driven: app.eval.m5_2_calibration_eval,
# run against Dataset A/B with the LITERAL annotated ground-truth box (the
# tightest possible "operator drew exactly the current digits" case),
# measured 39 confidently-wrong reconcile() confirmations on Dataset A
# (hr/rr) -- EVIDENCE.md sec 6.1's documented "too tight" hazard (a box
# sized to a momentary digit count later clips a longer reading) actually
# manifesting as unsafe confirmations, not just missed reads. The SAME
# script re-run with this exact 20% pad nearly halved that (39 -> 21) while
# *improving* Dataset B's OCR accuracy (34.7% -> 36.7%) and only mildly
# reducing Dataset A's (77.8% -> 72.9%, still comfortably above the
# ROADMAP.md M5.2 70% target). It does not reach zero -- see
# docs/M5_2_REAL_CALIBRATION_REPORT.md sec 9/16 for why padding alone is a
# PARTIAL mitigation, and what closes the remainder (Verify's live-crop
# preview at DRAW time, plus M5.3 tracking). Height is deliberately left
# unpadded -- a field's font size doesn't change with its value, only
# its digit COUNT (width) does.
WIDTH_SAFETY_PAD_FRACTION = 0.20


def pad_roi_boxes(roi_boxes: Dict[str, NormalizedBox], fraction: float = WIDTH_SAFETY_PAD_FRACTION) -> Dict[str, NormalizedBox]:
    """Grows every box symmetrically along its width by `fraction` (e.g.
    0.20 adds 10% on each side), clipped to stay inside [0, 1]. Applied
    once, server-side, at save time -- see WIDTH_SAFETY_PAD_FRACTION above."""
    if fraction <= 0:
        return roi_boxes
    padded: Dict[str, NormalizedBox] = {}
    for vital, box in roi_boxes.items():
        pad = box.w * fraction / 2
        x = max(0.0, box.x - pad)
        w = min(1.0 - x, box.w + 2 * pad)
        padded[vital] = NormalizedBox(x=x, y=box.y, w=w, h=box.h)
    return padded


def reference_pixel_boxes(profile: CalibrationProfile) -> Dict[str, Tuple[float, float, float, float]]:
    """The calibrated boxes expressed in the REFERENCE frame's own pixels --
    the coordinate space app.pipeline.layout_tracker's transform maps FROM.
    (The untracked path never needs this: it rescales normalized coordinates
    straight onto the live frame instead.)"""
    rw, rh = float(profile.reference_width), float(profile.reference_height)
    return {v: (b.x * rw, b.y * rh, b.w * rw, b.h * rh) for v, b in profile.roi_boxes.items()}


def _crop_pixel_boxes(
    img: np.ndarray, boxes: Dict[str, Tuple[float, float, float, float]], engine: str
) -> Dict[str, Optional[VitalRoiResult]]:
    h, w = img.shape[:2]
    results: Dict[str, Optional[VitalRoiResult]] = {}
    for vital, (bx, by, bw, bh) in boxes.items():
        x0, y0 = max(0, int(round(bx))), max(0, int(round(by)))
        x1, y1 = min(w, int(round(bx + bw))), min(h, int(round(by + bh)))
        if x1 <= x0 or y1 <= y0:
            results[vital] = None
            continue
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            results[vital] = None
            continue
        results[vital] = VitalRoiResult(
            box=(x0, y0, x1 - x0, y1 - y0), crop=crop.copy(), source_colour=None, engine=engine
        )
    return results


def make_extractor(
    profile: CalibrationProfile,
    tracker: Optional["LayoutTracker"] = None,
    on_tracking_result: Optional[Callable[["TrackingResult"], None]] = None,
) -> Callable[[np.ndarray], Dict[str, Optional[VitalRoiResult]]]:
    """Binds one saved CalibrationProfile into a RoiExtractor closure
    matching read_frame.RoiExtractor's Callable[[np.ndarray], Dict[...]]
    contract -- the actual swap point (read_frame(roi_extractor=...),
    CameraSource(roi_extractor=...); see app.ws.vitals._camera_roi_extractor
    for where the live path builds one from the DB's active profile).

    tracker (M5.3, optional): when supplied, the calibrated boxes are
    re-anchored onto every frame through app.pipeline.layout_tracker before
    cropping, so the reading survives camera/monitor movement. When None,
    behaviour is byte-for-byte M5.2's -- the static path below is untouched,
    which is also the rollback lever (LAYOUT_TRACKING=off).

    FAIL-CLOSED CONTRACT. If tracking is enabled and does not produce a
    transform this module is willing to stand behind, EVERY field is
    withheld for that frame. It never falls back to the static M5.2 box, and
    never reuses a previous frame's transform: both would crop from a region
    nothing has verified still contains that field, which is precisely the
    "confidently wrong" failure this project's safety posture exists to
    prevent (docs/ARCHITECTURE.md). Withholding surfaces downstream as
    confidence 0.0 -> reconcile() holding the last confirmed value and
    flagging it -- the existing, unmodified behaviour.

    on_tracking_result: observer for the eval harness / WS lock indicator.
    Called once per frame, including on failure. Must not raise.
    """
    tracked_engine = "calibrated_tracked"
    ref_boxes = reference_pixel_boxes(profile) if tracker is not None else {}

    def _withhold_all() -> Dict[str, Optional[VitalRoiResult]]:
        return {vital: None for vital in profile.roi_boxes}

    def _emit(result: "TrackingResult") -> None:
        if on_tracking_result is None:
            return
        try:
            on_tracking_result(result)
        except Exception:  # an observer must never break the pipeline
            logger.exception("calibrated ROI: tracking observer raised")

    def _extract(img: np.ndarray) -> Dict[str, Optional[VitalRoiResult]]:
        h, w = img.shape[:2]
        if h == 0 or w == 0:
            return _withhold_all()

        if tracker is None:
            # ── M5.2 static path, unchanged ──
            drift = aspect_ratio_drift(profile.reference_width, profile.reference_height, w, h)
            if drift > MAX_ASPECT_RATIO_DRIFT:
                logger.warning(
                    "calibrated ROI: frame aspect ratio drifted %.0f%% from the calibration reference "
                    "(%dx%d vs reference %dx%d) -- withholding all fields rather than mapping boxes onto "
                    "a frame calibration doesn't describe. Recalibration is likely required.",
                    drift * 100, w, h, profile.reference_width, profile.reference_height,
                )
                return _withhold_all()
            return extract_rois_from_boxes(img, profile.roi_boxes)

        # ── M5.3 tracked path ──
        # No aspect-drift guard here, deliberately: that heuristic exists
        # because a STATIC box cannot survive a re-framed camera. A similarity
        # transform verified by >=20 RANSAC inliers and explicit scale/
        # rotation/translation bounds is strictly stronger evidence about this
        # frame than an aspect-ratio rule of thumb, and it re-anchors the box
        # instead of merely rejecting the frame.
        result = tracker.track(img)
        _emit(result)
        if not result.ok:
            logger.info(
                "layout tracking did not lock (%s: %s) -- withholding all fields for this frame",
                result.status.value, "; ".join(result.reject_reasons) or "no detail",
            )
            return _withhold_all()

        transformed = {v: transform_box(b, result.transform) for v, b in ref_boxes.items()}
        geometry_errors = check_transformed_rois(transformed, ref_boxes, w, h)
        if geometry_errors:
            logger.info(
                "layout tracking produced unusable ROI geometry (%s) -- withholding all fields",
                "; ".join(geometry_errors),
            )
            _emit(
                TrackingResult(
                    status=TrackingStatus.ROI_GEOMETRY_FAILURE,
                    n_reference_keypoints=result.n_reference_keypoints,
                    n_frame_keypoints=result.n_frame_keypoints,
                    n_matches=result.n_matches,
                    n_inliers=result.n_inliers,
                    inlier_ratio=result.inlier_ratio,
                    mean_reprojection_error=result.mean_reprojection_error,
                    scale=result.scale,
                    rotation_deg=result.rotation_deg,
                    translation_px=result.translation_px,
                    reject_reasons=tuple(geometry_errors),
                    elapsed_ms=result.elapsed_ms,
                )
            )
            return _withhold_all()

        return _crop_pixel_boxes(img, transformed, tracked_engine)

    return _extract
