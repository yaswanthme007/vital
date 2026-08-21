"""M3 Phase 2/3: the Tier-2 ROI stage -- candidate generation -> FieldCNN
classification -> per-vital candidate selection. Same contract as
app.pipeline.roi.extract_rois_by_colour: takes an RGB image, returns
Dict[str, Optional[VitalRoiResult]], one entry per app.pipeline.read_frame
.VITALS. This is what makes it a drop-in swap inside read_frame() -- nothing
downstream (OCR, reconcile, alerts, persistence, WebSocket) needs to know or
care which stage produced the dict.

Candidate generation reuses app.eval.tier2_candidates.adaptive_threshold_
candidates_v2 unmodified -- the M1.1-hardened, recommended generator (90.5%
recall on the real external-video benchmark; see
TIER2_M1_1_HARDENING_REPORT.md sec 8/11). This is a deliberate, first-time
crossing of app/eval's historical "isolated from production" boundary
(every M1/M1.1/M2 report states nothing under app/eval is imported by
app.pipeline.*) -- now that the generator has graduated from spike/eval into
an opt-in production stage, that invariant is intentionally relaxed for this
one, already-validated, byte-for-byte-untouched function. Nothing else under
app/eval is imported here (no training code, no torch).
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.eval.tier2_candidates import adaptive_threshold_candidates_v2
from app.eval.tier2_common import Box, iou
from app.pipeline.field_classifier import NOT_A_VITAL, FieldClassifierEngine, FieldPrediction, get_default_field_classifier
from app.pipeline.types import VitalRoiResult

logger = logging.getLogger(__name__)

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

# ─── Configuration (Phase 5: "make thresholds configurable, document why") ──
#
# MIN_CLASSIFIER_CONFIDENCE: below this, a candidate is never associated
# with any vital, no matter what FieldCNN's argmax label says -- same "never
# guess" contract onnx_engine.py's MIN_READABLE_CONFIDENCE already enforces
# one stage downstream. 0.5 is a DESIGN CHOICE, not a clinically-derived
# threshold: chance level on this 7-class problem is ~14%, so 50% is already
# well clear of a random guess, and it sits below the M2 test-set precision
# range (75-100%) for every real vital class (TIER2_M2_FIELD_CLASSIFIER_
# REPORT.md sec 11) -- meaning it's unlikely to reject a correct, real
# detection outright, while still filtering out the model's weakest calls.
# It will NOT catch a confidently-wrong prediction (M2 sec 14's RR-rejected-
# at-94.5% and HR-fragment-accepted-at-98.7% cases are both ABOVE this
# threshold by design) -- that is a documented model/data limitation, not
# something a threshold value can fix (see the M3 report's Known
# Limitations section; Phase 5 of the M3 task explicitly says not to "fix"
# this by moving the threshold).
MIN_CLASSIFIER_CONFIDENCE = float(os.environ.get("TIER2_MIN_CLASSIFIER_CONFIDENCE", "0.5"))

# DEDUPE_IOU: two candidates predicted the same vital and overlapping more
# than this are treated as the same physical detection (e.g. the two
# threshold-polarity passes inside adaptive_threshold_candidates_v2
# occasionally survive as slightly different boxes even after that
# function's own internal 0.7 dedupe) -- keep only the higher-confidence one.
DEDUPE_IOU = float(os.environ.get("TIER2_DEDUPE_IOU", "0.5"))

# COMPETING_MARGIN: after dedupe, if two or more SPATIALLY DISTINCT
# candidates both predicted the same vital and their confidences are within
# this margin of each other, treat it as genuine competing evidence rather
# than picking a near-arbitrary "winner" -- the vital is left unresolved
# (None) for this frame rather than guessing, matching Phase 5's explicit
# "do not allow two candidates to silently become two HR readings" and "if
# ... multiple candidates strongly compete for the same vital ... the result
# should remain uncertain" instructions. 0.15 is an unvalidated, conservative
# default (documented limitation, sec below) -- this dataset's test set
# rarely produces two simultaneous same-vital candidates above
# MIN_CLASSIFIER_CONFIDENCE, so there is not yet real data to tune this
# against; it errs toward caution (a wider margin => more frames resolve to
# "uncertain" rather than guessing) rather than toward maximizing recall.
COMPETING_MARGIN = float(os.environ.get("TIER2_COMPETING_MARGIN", "0.15"))


def _clip_box(box: Box, w: int, h: int) -> Optional[Tuple[int, int, int, int]]:
    x, y, bw, bh = box
    x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
    x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def _select_candidate_for_vital(
    candidates: List[Tuple[Tuple[int, int, int, int], FieldPrediction]]
) -> Tuple[Optional[Tuple[int, int, int, int]], Optional[FieldPrediction], Optional[str]]:
    """Phase 3's selection strategy, applied to one vital's already-
    filtered (predicted==vital, confidence>=MIN_CLASSIFIER_CONFIDENCE)
    candidate list. Deterministic, evidence-based, no invented tracking:

      1. Sort by classifier confidence, descending (ties broken by box
         geometry -- smaller x0 first -- so the result never depends on
         Python's incidental list/argmax ordering).
      2. Greedily keep a candidate only if it does NOT overlap
         (IoU > DEDUPE_IOU) an already-kept, higher-confidence candidate --
         this collapses near-duplicate detections of the same physical
         region into one, keeping the classifier's most confident box.
      3. If exactly one spatially-distinct candidate survives, use it.
      4. If two or more survive and the top two confidences are within
         COMPETING_MARGIN of each other, this is genuine competing
         evidence for two DIFFERENT locations both claiming to be this
         vital (e.g. a fragment plus the real reading, or two decoys) --
         resolve to "no candidate" rather than pick one arbitrarily.
      5. Otherwise (top candidate has a clear confidence lead) use it --
         geometry-distinct candidates existing doesn't by itself make the
         result uncertain if one is decisively more confident than the rest.

    Returns (box, prediction, reason) -- reason is None on a clean single
    resolution, "competing_candidates" when step 4 fired, useful for
    debugging/logging without needing a second data structure.
    """
    if not candidates:
        return None, None, None

    ordered = sorted(candidates, key=lambda c: (-c[1].confidence, c[0][0]))
    kept: List[Tuple[Tuple[int, int, int, int], FieldPrediction]] = []
    for box, pred in ordered:
        box_f: Box = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        if any(iou(box_f, (float(k[0][0]), float(k[0][1]), float(k[0][2]), float(k[0][3]))) > DEDUPE_IOU for k in kept):
            continue
        kept.append((box, pred))

    if len(kept) == 1:
        return kept[0][0], kept[0][1], None

    top, runner_up = kept[0], kept[1]
    if (top[1].confidence - runner_up[1].confidence) < COMPETING_MARGIN:
        return None, None, "competing_candidates"
    return top[0], top[1], None


def extract_rois_by_field_classifier(
    img: np.ndarray,
    classifier: Optional[FieldClassifierEngine] = None,
) -> Dict[str, Optional[VitalRoiResult]]:
    """The Tier-2 ROI stage: candidate generation -> FieldCNN -> per-vital
    selection. Same signature/return contract as
    app.pipeline.roi.extract_rois_by_colour so read_frame() can swap between
    them with zero downstream changes.

    img: RGB uint8 ndarray (post screen-detection/rectification -- same
    image extract_rois_by_colour would receive at the same call site).
    """
    classifier = classifier or get_default_field_classifier()
    h, w = img.shape[:2]

    raw_candidates = adaptive_threshold_candidates_v2(img)

    boxes: List[Tuple[int, int, int, int]] = []
    crops: List[np.ndarray] = []
    for box in raw_candidates:
        clipped = _clip_box(box, w, h)
        if clipped is None:
            continue
        x, y, bw, bh = clipped
        crops.append(img[y : y + bh, x : x + bw])
        boxes.append(clipped)

    predictions = classifier.classify(crops)

    per_vital_candidates: Dict[str, List[Tuple[Tuple[int, int, int, int], FieldPrediction]]] = {v: [] for v in VITALS}
    for box, pred in zip(boxes, predictions):
        if pred.label == NOT_A_VITAL or pred.label not in per_vital_candidates:
            continue
        if pred.confidence < MIN_CLASSIFIER_CONFIDENCE:
            continue
        per_vital_candidates[pred.label].append((box, pred))

    results: Dict[str, Optional[VitalRoiResult]] = {}
    for vital in VITALS:
        box, pred, reason = _select_candidate_for_vital(per_vital_candidates[vital])
        if box is None or pred is None:
            if reason == "competing_candidates":
                logger.info("tier2 roi: %s left unresolved -- competing candidates within margin", vital)
            results[vital] = None
            continue
        x, y, bw, bh = box
        crop = img[y : y + bh, x : x + bw].copy()
        results[vital] = VitalRoiResult(
            box=(x, y, bw, bh),
            crop=crop,
            source_colour=None,
            engine="tier2_fieldcnn",
            classifier_confidence=pred.confidence * 100.0,
        )
    return results
