"""M5.3: per-frame layout tracking -- re-anchoring an operator's calibrated
ROI boxes onto the monitor wherever it has moved to in the current frame.

WHAT PROBLEM THIS SOLVES. M5.2 gave the operator a way to say "the six vital
fields are HERE", stored as normalized boxes against one reference frame
(app.models.calibration.CalibrationProfile). Those boxes are STATIC: if the
camera or the monitor moves, the boxes stay where they were and start
cropping the wrong pixels. docs/EVIDENCE.md sec 6 measured exactly this on
Dataset B -- a fixed calibrated box scores mean IoU 0.54, and re-anchoring it
per frame against the monitor's static chrome scores 0.68 with OCR accuracy
rising 35% -> 57%.

WHAT THIS MODULE IS NOT. It is not a detector: it never decides *what* a
field is, never proposes a box, and never reads a value. It answers exactly
one question -- "where has the thing I was calibrated against moved to?" --
and answers it with a transform or with an explicit refusal. There is no
"best guess" path: every way this can fail produces a named status, and
app.pipeline.calibrated_roi turns any non-OK status into withheld fields
rather than a crop from an unverified region.

TRACK THE CHROME, NOT THE DIGITS. docs/ROADMAP.md M5.3 records that a first
attempt templating the digit boxes themselves tracked WORSE than not tracking
at all (IoU 0.51 vs 0.54), because the digits are precisely what changes
between frames. from_reference_image(exclude_boxes=...) therefore masks the
calibrated ROI regions out of the reference feature set, so the anchor is the
monitor's printed labels, panel borders and bezel.

WHY A SIMILARITY TRANSFORM AND NOT A HOMOGRAPHY. Measured on the real
Dataset B recording, genuine camera motion in this material is pan + zoom +
a few degrees of roll (scale 0.51-2.08, rotation within +/-11 deg). A
homography's 8 degrees of freedom are badly under-constrained at the 24-100
inliers those hard frames actually produce, and an under-constrained
homography fails by warping a crop into nonsense rather than by refusing --
the worst failure shape for this system. estimateAffinePartial2D's 4 DOF
(translation, rotation, uniform scale) cannot express shear or perspective at
all, so a bad estimate stays geometrically sane enough for the bounds below
to catch it. The cost is that genuine perspective change is not corrected;
it degrades as rising reprojection error, and is a documented limitation.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

PixelBox = Tuple[float, float, float, float]  # x, y, w, h


class TrackingStatus(str, Enum):
    """Every distinct way tracking can end. Deliberately NOT collapsed into a
    bare None: docs/ROADMAP.md's acceptance criterion is that lock loss is
    *reported*, and an operator staring at a held value needs to know whether
    the monitor left the frame, the lens is smeared, or the geometry came back
    nonsense. Callers withhold on anything that is not OK; the distinction is
    for humans and for the eval harness."""

    OK = "ok"
    NO_REFERENCE_FEATURES = "no_reference_features"
    NO_FRAME_FEATURES = "no_frame_features"
    LOW_FEATURE_MATCHES = "low_feature_matches"
    ESTIMATION_FAILED = "estimation_failed"
    LOW_INLIER_COUNT = "low_inlier_count"
    HIGH_REPROJECTION_ERROR = "high_reprojection_error"
    INVALID_SCALE = "invalid_scale"
    INVALID_ROTATION = "invalid_rotation"
    INVALID_TRANSLATION = "invalid_translation"
    ROI_GEOMETRY_FAILURE = "roi_geometry_failure"


# ─── thresholds ───────────────────────────────────────────────────────────
#
# PROVENANCE OF THESE NUMBERS. Each was chosen from a measured separation
# between genuine tracking and deliberate negative controls, run during M5.3
# Phase 0 against the real Dataset A/B frames (see
# docs/M5_3_LAYOUT_TRACKING_REPORT.md for the full tables). They are
# CONSERVATIVE INITIAL values in the sense that they are set to reject
# everything the negative controls produced while keeping every genuine frame
# measured so far -- not fitted to maximise any accuracy number. The eval
# harness emits the observed distribution of all of them so they can be
# re-derived rather than guessed at again.

# Raw crossCheck matches before RANSAC. A heavily blurred (occluded-lens)
# negative control produced 13; genuine frames produced 451-3425.
MIN_RAW_MATCHES = 30

# RANSAC inliers. This is the LOAD-BEARING gate. Negative controls produced
# 3 (blur), 4 (pure noise) and 11 (a different monitor entirely); genuine
# frames produced >=24. On the dense Dataset B recording, frames yielding
# 9-13 inliers returned transforms whose rotation swung +/-10 deg between
# adjacent half-second samples -- i.e. visibly unreliable -- while frames at
# >=50 inliers were stable. 20 sits in that measured gap.
MIN_INLIERS = 20

# Inlier RATIO is recorded but deliberately NOT gated. Measured: genuine
# large-motion frames -- exactly the ones tracking exists to fix -- run at
# ratio 0.03-0.11, BELOW the 0.23 produced by a blurred negative control.
# Gating on ratio would reject the useful frames and admit a bad one. This
# constant exists to document that choice, not to be applied.
INLIER_RATIO_IS_NOT_A_GATE = True

# Mean reprojection error over inliers, in reference pixels. A weak
# discriminator on its own (genuine 0.2-2.7px, negatives 1.2-2.6px), so it is
# set loose and used only to catch grossly inconsistent fits.
MAX_REPROJECTION_ERROR_PX = 4.0

# Uniform scale between reference and current frame. Genuine motion measured
# 0.51-2.08; negative controls produced 0.058 and 0.71. The lower bound is
# what catches the degenerate collapse; the upper bound allows a real
# walk-up-to-the-monitor zoom with margin.
MIN_SCALE = 0.4
MAX_SCALE = 2.5

# Roll. Genuine motion measured within +/-11 deg; unrelated-image negative
# controls produced 140 deg and 158 deg. A real ceiling-mounted monitor and a
# handheld camera do not produce more than this without the operator noticing.
MAX_ROTATION_DEG = 20.0

# Translation, as a multiple of the reference frame's diagonal. Guards the
# pathological "monitor is 900px off-screen" case the brief calls out.
MAX_TRANSLATION_DIAGONALS = 1.5

# ─── transformed-ROI geometry gates ───────────────────────────────────────
# Applied AFTER the transform passes, to the boxes it actually produced --
# a transform can be numerically plausible and still put a crop somewhere
# useless.
MIN_ROI_VISIBLE_FRACTION = 0.6   # at least this much of the box inside the frame
MIN_ROI_AREA_RATIO = 0.25        # vs the calibrated box, in reference px
MAX_ROI_AREA_RATIO = 4.0

# ─── feature extraction ───────────────────────────────────────────────────
# Detection and matching run at this working resolution; frames already
# smaller are left alone. Both values were chosen from a measured sweep over
# the dense Dataset B recording at a realistic live-camera resolution
# (1280x720), scoring lock rate against per-frame cost:
#
#   max_dim  features   lock rate   mean ms
#       480      2000       74.1%      21
#       480      4000       74.1%      97
#       640      3000       98.1%     139
#       640      4000      100.0%     122     <-- shipped
#       960      4000       98.1%     235
#
# 640 halves the cost of 960 while slightly IMPROVING lock, because a smaller
# working image suppresses high-frequency spurious features that RANSAC then
# has to reject. Dropping to 480 would meet docs/ROADMAP.md's "<50ms/frame"
# target but loses 26% of locks -- and under this module's fail-closed
# contract a lost lock withholds EVERY vital for that frame, so buying
# latency with lock rate is the wrong trade for a system whose entire value
# is a continuous, trustworthy reading. The resulting ~120ms/frame misses
# that 50ms target and is reported as such in
# docs/M5_3_LAYOUT_TRACKING_REPORT.md; end-to-end still fits ROADMAP's
# 1.5s/frame budget because Tesseract (~1s for six fields) dominates.
TRACK_MAX_DIM = 640
ORB_FEATURES = 4000

# OpenCV's RANSAC draws from a global RNG. Seeding it immediately before each
# estimation makes track() reproducible for a given (reference, frame) pair,
# which is what the determinism test pins. Nothing else in this codebase
# consumes cv2's RNG, so the global write is safe here.
RANSAC_SEED = 20260819
RANSAC_REPROJ_THRESHOLD = 5.0
RANSAC_MAX_ITERS = 5000
RANSAC_CONFIDENCE = 0.995


@dataclass(frozen=True)
class TrackingResult:
    """Outcome of one track() call. `transform` is a 2x3 similarity mapping
    REFERENCE-image pixels to CURRENT-frame pixels, both in original (not
    downscaled) coordinates, and is None for every status except OK."""

    status: TrackingStatus
    transform: Optional[np.ndarray] = None
    n_reference_keypoints: int = 0
    n_frame_keypoints: int = 0
    n_matches: int = 0
    n_inliers: int = 0
    inlier_ratio: float = 0.0
    mean_reprojection_error: float = 0.0
    scale: float = 0.0
    rotation_deg: float = 0.0
    translation_px: float = 0.0
    reject_reasons: Tuple[str, ...] = ()
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status is TrackingStatus.OK and self.transform is not None

    def to_dict(self) -> dict:
        """Flat, JSON-safe form for eval artifacts and the WS envelope."""
        return {
            "status": self.status.value,
            "ok": self.ok,
            "nReferenceKeypoints": self.n_reference_keypoints,
            "nFrameKeypoints": self.n_frame_keypoints,
            "nMatches": self.n_matches,
            "nInliers": self.n_inliers,
            "inlierRatio": round(self.inlier_ratio, 4),
            "meanReprojectionError": round(self.mean_reprojection_error, 3),
            "scale": round(self.scale, 4),
            "rotationDeg": round(self.rotation_deg, 3),
            "translationPx": round(self.translation_px, 2),
            "rejectReasons": list(self.reject_reasons),
            "elapsedMs": round(self.elapsed_ms, 2),
        }


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def _downscale(gray: np.ndarray, max_dim: int = TRACK_MAX_DIM) -> Tuple[np.ndarray, float]:
    """Returns (image, scale) where scale maps ORIGINAL px -> working px."""
    h, w = gray.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return gray, 1.0
    s = max_dim / longest
    return cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), s


def transform_box(box: PixelBox, M: np.ndarray) -> PixelBox:
    """Maps an (x, y, w, h) box through a 2x3 affine, returning the
    axis-aligned bounding box of the transformed corners. Axis-aligned
    because every downstream consumer -- the crop, the OCR preprocessor,
    VitalRoiResult.box -- is axis-aligned; a rotated crop would have to be
    re-rendered, and MAX_ROTATION_DEG keeps the discrepancy small."""
    x, y, w, h = box
    corners = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]).reshape(-1, 1, 2)
    t = cv2.transform(corners, M).reshape(-1, 2)
    nx, ny = float(t[:, 0].min()), float(t[:, 1].min())
    return nx, ny, float(t[:, 0].max() - nx), float(t[:, 1].max() - ny)


def check_transformed_rois(
    transformed: Dict[str, PixelBox],
    reference: Dict[str, PixelBox],
    frame_w: int,
    frame_h: int,
) -> List[str]:
    """Phase 6's post-transform defence layer. A transform can satisfy every
    numeric bound above and still land a crop off-screen, shrunk to nothing,
    or on top of a neighbouring field. Returns human-readable reasons; empty
    means the geometry is usable.

    Deliberately mirrors the vocabulary of
    app.pipeline.calibration_validate, which already enforces the analogous
    rules at calibration time -- the same geometric mistakes are unacceptable
    whether an operator drew them or a transform produced them."""
    reasons: List[str] = []

    for vital, box in transformed.items():
        x, y, w, h = box
        if w <= 0 or h <= 0:
            reasons.append(f"{vital}: transformed box collapsed to zero size")
            continue

        vx0, vy0 = max(0.0, x), max(0.0, y)
        vx1, vy1 = min(float(frame_w), x + w), min(float(frame_h), y + h)
        visible = max(0.0, vx1 - vx0) * max(0.0, vy1 - vy0)
        if visible / (w * h) < MIN_ROI_VISIBLE_FRACTION:
            reasons.append(
                f"{vital}: only {visible / (w * h) * 100:.0f}% of the tracked box is inside the frame"
            )

        ref = reference.get(vital)
        if ref is not None and ref[2] > 0 and ref[3] > 0:
            ratio = (w * h) / (ref[2] * ref[3])
            if ratio < MIN_ROI_AREA_RATIO or ratio > MAX_ROI_AREA_RATIO:
                reasons.append(f"{vital}: tracked box area changed {ratio:.2f}x vs calibration")

    items = list(transformed.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (v1, b1), (v2, b2) = items[i], items[j]
            x0, y0 = max(b1[0], b2[0]), max(b1[1], b2[1])
            x1 = min(b1[0] + b1[2], b2[0] + b2[2])
            y1 = min(b1[1] + b1[3], b2[1] + b2[3])
            inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            smaller = min(b1[2] * b1[3], b2[2] * b2[3])
            if smaller > 0 and inter / smaller > 0.3:
                reasons.append(f"{v1} and {v2} tracked boxes overlap {inter / smaller * 100:.0f}%")

    return reasons


class LayoutTracker:
    """Holds ONE reference frame's features and matches later frames against
    them. Feature extraction on the reference happens once, at construction:
    app.ws.vitals builds a tracker per WebSocket connection, not per frame,
    so the ~50-300ms detection cost is paid once per case rather than 1 Hz."""

    def __init__(
        self,
        keypoints: Sequence["cv2.KeyPoint"],
        descriptors: Optional[np.ndarray],
        work_scale: float,
        reference_shape: Tuple[int, int],
        n_features: int = ORB_FEATURES,
    ):
        self._keypoints = keypoints
        self._descriptors = descriptors
        self._work_scale = work_scale
        self.reference_height, self.reference_width = reference_shape
        # Detector and matcher are built ONCE and reused for every frame.
        # Measured: constructing a fresh cv2.ORB_create per call cost ~130ms
        # of the 196ms per-frame total on a 640x360 frame -- more than the
        # matching it was feeding. Reusing one detector takes the same work
        # to ~62ms at identical feature count, lock rate and inlier counts.
        # Safe because a tracker belongs to exactly one WebSocket connection
        # and CameraSource reads one frame at a time on that connection.
        self._orb = cv2.ORB_create(nfeatures=n_features)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self._diagonal = float(np.hypot(self.reference_width, self.reference_height))

    # ── construction ─────────────────────────────────────────────────────

    @classmethod
    def from_reference_image(
        cls,
        img: np.ndarray,
        exclude_boxes: Optional[Sequence[PixelBox]] = None,
        max_dim: int = TRACK_MAX_DIM,
        n_features: int = ORB_FEATURES,
    ) -> "LayoutTracker":
        """`exclude_boxes` are the calibrated ROI boxes in REFERENCE pixel
        coordinates. They are masked OUT of the reference feature set so the
        anchor is the monitor's static chrome rather than the digits -- see
        the module docstring for the measurement behind that choice."""
        gray = _to_gray(img)
        h, w = gray.shape[:2]
        work, scale = _downscale(gray, max_dim)

        mask = None
        if exclude_boxes:
            mask = np.full(work.shape[:2], 255, dtype=np.uint8)
            for (bx, by, bw, bh) in exclude_boxes:
                x0 = max(0, int(bx * scale))
                y0 = max(0, int(by * scale))
                x1 = min(work.shape[1], int((bx + bw) * scale))
                y1 = min(work.shape[0], int((by + bh) * scale))
                if x1 > x0 and y1 > y0:
                    mask[y0:y1, x0:x1] = 0

        orb = cv2.ORB_create(nfeatures=n_features)
        keypoints, descriptors = orb.detectAndCompute(work, mask)
        return cls(keypoints or [], descriptors, scale, (h, w), n_features=n_features)

    @property
    def n_reference_keypoints(self) -> int:
        return len(self._keypoints)

    # ── per-frame tracking ───────────────────────────────────────────────

    def track(self, frame: np.ndarray) -> TrackingResult:
        """Estimates where the reference layout has moved to in `frame`.
        Never raises on a bad/degenerate frame and never returns a transform
        it could not justify."""
        t0 = time.perf_counter()

        def done(status: TrackingStatus, **kw) -> TrackingResult:
            return TrackingResult(
                status=status,
                n_reference_keypoints=len(self._keypoints),
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                **kw,
            )

        if self._descriptors is None or len(self._descriptors) < 4:
            return done(TrackingStatus.NO_REFERENCE_FEATURES,
                        reject_reasons=("reference frame yielded too few ORB features",))

        gray = _to_gray(frame)
        if gray.size == 0:
            return done(TrackingStatus.NO_FRAME_FEATURES, reject_reasons=("empty frame",))
        work, frame_scale = _downscale(gray, TRACK_MAX_DIM)

        kp_f, desc_f = self._orb.detectAndCompute(work, None)
        n_frame_kp = len(kp_f or [])
        if desc_f is None or len(desc_f) < 4:
            return done(TrackingStatus.NO_FRAME_FEATURES, n_frame_keypoints=n_frame_kp,
                        reject_reasons=("current frame yielded too few ORB features",))

        matches = self._matcher.match(self._descriptors, desc_f)
        n_matches = len(matches)
        if n_matches < max(4, MIN_RAW_MATCHES):
            return done(TrackingStatus.LOW_FEATURE_MATCHES, n_frame_keypoints=n_frame_kp,
                        n_matches=n_matches,
                        reject_reasons=(f"only {n_matches} feature matches (need {MIN_RAW_MATCHES})",))

        src = np.float32([self._keypoints[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
        dst = np.float32([kp_f[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)

        cv2.setRNGSeed(RANSAC_SEED)
        M_work, inliers = cv2.estimateAffinePartial2D(
            src, dst,
            method=cv2.RANSAC,
            ransacReprojThreshold=RANSAC_REPROJ_THRESHOLD,
            maxIters=RANSAC_MAX_ITERS,
            confidence=RANSAC_CONFIDENCE,
        )
        if M_work is None or inliers is None:
            return done(TrackingStatus.ESTIMATION_FAILED, n_frame_keypoints=n_frame_kp,
                        n_matches=n_matches, reject_reasons=("RANSAC could not fit a similarity",))

        inlier_mask = inliers.ravel().astype(bool)
        n_inliers = int(inlier_mask.sum())
        ratio = n_inliers / n_matches

        # Re-express the working-resolution transform in ORIGINAL pixels:
        # reference px --(self._work_scale)--> ref work px --(M_work)-->
        # frame work px --(1/frame_scale)--> frame px.
        M = M_work.copy()
        M[:, :2] *= (self._work_scale / frame_scale)
        M[:, 2] /= frame_scale

        scale = float(np.hypot(M[0, 0], M[1, 0]))
        rotation = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
        translation = float(np.hypot(M[0, 2], M[1, 2]))

        projected = cv2.transform(src, M_work).reshape(-1, 2)
        residuals = np.linalg.norm(projected[inlier_mask] - dst.reshape(-1, 2)[inlier_mask], axis=1)
        reproj = float(residuals.mean()) if residuals.size else float("inf")

        stats = dict(
            n_frame_keypoints=n_frame_kp, n_matches=n_matches, n_inliers=n_inliers,
            inlier_ratio=ratio, mean_reprojection_error=reproj, scale=scale,
            rotation_deg=rotation, translation_px=translation,
        )

        if n_inliers < MIN_INLIERS:
            return done(TrackingStatus.LOW_INLIER_COUNT,
                        reject_reasons=(f"only {n_inliers} RANSAC inliers (need {MIN_INLIERS})",), **stats)
        if reproj > MAX_REPROJECTION_ERROR_PX:
            return done(TrackingStatus.HIGH_REPROJECTION_ERROR,
                        reject_reasons=(f"mean reprojection error {reproj:.2f}px "
                                        f"(max {MAX_REPROJECTION_ERROR_PX})",), **stats)
        if not (MIN_SCALE <= scale <= MAX_SCALE):
            return done(TrackingStatus.INVALID_SCALE,
                        reject_reasons=(f"scale {scale:.3f} outside [{MIN_SCALE}, {MAX_SCALE}]",), **stats)
        if abs(rotation) > MAX_ROTATION_DEG:
            return done(TrackingStatus.INVALID_ROTATION,
                        reject_reasons=(f"rotation {rotation:.1f}deg exceeds {MAX_ROTATION_DEG}deg",), **stats)
        if translation > MAX_TRANSLATION_DIAGONALS * self._diagonal:
            return done(TrackingStatus.INVALID_TRANSLATION,
                        reject_reasons=(f"translation {translation:.0f}px exceeds "
                                        f"{MAX_TRANSLATION_DIAGONALS} frame diagonals",), **stats)

        return done(TrackingStatus.OK, transform=M, **stats)
