"""M5.7.2: multi-frame burst verification for Calibration's Verify step.

============================================================================
PROBLEM THIS FIXES
============================================================================

A correctly-drawn calibration box can still return an unreadable/wrong OCR
result on any ONE frame — transient glare, motion blur from a hand still
settling on the camera, a momentary autofocus hunt, or a single bad JPEG
compression pass can all degrade one capture without the box or the monitor
having moved at all. Pre-M5.7.2, Calibration's Verify step captured exactly
one frame and trusted it outright.

============================================================================
DESIGN: WHY THIS IS NOT JUST "VOTE ACROSS N FRAMES"
============================================================================

app.validation.temporal (M5.4) already tried the naive version of this idea
for the LIVE path — "N consecutive identical reads corroborate each other"
— and M5.4's own held-out validation (Phase 6, docs/
M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md) found a real blind spot: a
SYSTEMATIC misread (the same too-narrow crop truncating the same digit the
same way every tick) produces IDENTICAL wrong values run after run, which
looks exactly like genuine agreement to a pure repetition check. That
mechanism shipped OFF by default for exactly this reason.

A burst of consecutive frames from a STATIC calibration screen has the same
exposure: if the crop geometry itself is bad (too narrow, off-frame), every
frame in the burst will show the SAME truncation, and naive majority-vote
would report high "agreement" for a wrong value. Two things here defend
against that, both reusing evidence this codebase already validated rather
than inventing new heuristics:

  1. app.validation.crop_integrity.has_residual_content (M5.4.1) — the same
     signal that closed temporal corroboration's exact blind spot. A value
     only counts as stable here if EVERY agreeing sample's raw OCR text has
     no residual content beyond the parsed value (i.e. Tesseract isn't
     quietly discarding a clipped remainder on every one of them).
  2. Requiring the agreeing samples' mean confidence to clear
     app.validation.rules.CONFIDENCE_MEDIUM_MIN — the SAME threshold the
     live path already gates on, not a new number invented for this
     feature. See section 5 below for why lowering it was never an option.

What burst verification legitimately defends against: independent,
per-frame noise (glare, blur, momentary exposure shift, compression
artifacts) — exactly what a real burst of frames captured moments apart
actually varies. What it does NOT defend against, and does not claim to:
persistent miscalibration (a box drawn in the wrong place, or too narrow
for the digit count) — that is what app.pipeline.calibrated_roi's own
WIDTH_SAFETY_PAD_FRACTION and the operator's own visual box-drawing exist
to prevent, and what the crop-integrity check above still catches when it
slips through.

============================================================================
REUSE, NOT REPLACEMENT
============================================================================

This module is deliberately thin. It calls the SAME production
OcrEngine.read_vital_with_diagnostics (app.pipeline.ocr, real Tesseract,
real _preprocess) on crops extracted by the SAME
app.pipeline.calibrated_roi.extract_rois_from_boxes used everywhere else —
there is no second OCR system here. The only new production code is (a) the
'adaptive' preprocessing variant added to ocr._preprocess (a fallback
second opinion, invoked only on fields the primary pass can't stabilize —
see _aggregate_field below) and (b) the aggregation/consensus logic in this
file. Confidence gating reuses app.validation.rules.CONFIDENCE_MEDIUM_MIN
verbatim; crop-integrity reuses app.validation.crop_integrity verbatim.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.models.calibration import NormalizedBox
from app.pipeline.calibrated_roi import extract_rois_from_boxes
from app.pipeline.ocr import NibpValue, OcrEngine
from app.pipeline.read_frame import VITALS, get_default_engine
from app.validation.crop_integrity import crop_is_suspicious
from app.validation.rules import CONFIDENCE_MEDIUM_MIN
from app.validation.temporal import CONFIDENCE_TEMPORAL_FLOOR

# A value must be produced by a STRICT MAJORITY of the valid (non-None)
# samples collected for that field to even be considered — e.g. 3 of 5
# frames, not merely the single most-common value among many disagreeing
# ones. ">", not ">=", so exactly half (e.g. 2 of 4) is NOT a majority.
BURST_AGREEMENT_MIN_FRACTION = 0.5

# ============================================================================
# M5.7.2 HOTFIX: the corroborated-recovery tier
# ============================================================================
#
# ROOT CAUSE this addresses (real-camera demo, docs/M5_7_2_BURST_VERIFICATION
# hotfix report): the shipped rule required the AGREEING group's mean
# confidence to clear CONFIDENCE_MEDIUM_MIN (70) unconditionally -- the exact
# same bar a single, uncorroborated frame needs on the live path. That
# throws away the statistical benefit of having several independent frames
# agree at all: a value four different real camera captures agree on is
# objectively stronger evidence than one 70%-confidence frame, even if each
# individual capture's OCR confidence is more modest (a real webcam angled
# at a 7-segment monitor display routinely produces correct reads in the
# 45-65% Tesseract-confidence range -- this is not a defect in the frame,
# it is what real, non-studio OCR confidence looks like). Requiring 70
# regardless of how many frames corroborate it means burst verification
# bought no benefit over single-frame Verify for exactly the fields that
# need it most.
#
# This is NOT "lower the floor" (that was measured and rejected -- see
# "Which confidence floor" in docs/M5_7_2_BURST_VERIFICATION.md: a flat
# floor=40 roughly quadrupled the WRONG rate on real data, 0.7-1.1% ->
# 4.4-4.7%). Instead: a SECOND, narrower tier that only relaxes the
# confidence bar when agreement is a supermajority (at most one disagreeing/
# invalid sample out of a 5-frame primary burst), backed by a minimum
# absolute agreeing-sample count (so a 2-sample 100% "supermajority" isn't
# trusted as strongly as 4-of-5 is), and STILL requires every agreeing
# sample to be clean of residual content -- crop-integrity is not
# conditioned on which tier is being evaluated. The lower floor itself is
# CONFIDENCE_TEMPORAL_FLOOR, an EXISTING constant (app.validation.temporal,
# M5.4) already validated for exactly this "multiple corroborating reads
# justify trusting a lower per-read confidence than one untrusted tick
# would need" role -- not a new number invented for this feature.
#
# Worked example (the exact hotfix scenario): 90, 90, 90, 9, 90 -- 4 of 5
# valid samples agree on 90 (agreement_fraction = 0.8, clears the 0.8
# supermajority bar), the "9" is a single independent noisy outlier (does
# not poison the majority), and if the four agreeing 90s are individually
# clean and their mean confidence is, say, 58 (below 70, at or above 40),
# this tier now accepts it. A weaker 3-of-5 (0.6) agreement at the same 58
# confidence still does NOT qualify for recovery -- it falls back to
# needing the full 70 floor, same as before this hotfix -- because only
# a supermajority is treated as strong enough corroboration to relax the
# per-sample bar. This preserves the "strong clean agreement should survive
# isolated noisy frames" rule without becoming "accept anything that looks
# common".
BURST_SUPERMAJORITY_FRACTION = 0.8

# Even at 100% agreement, don't apply the recovery tier off of a tiny
# sample -- e.g. 1-of-1 or 2-of-2 "supermajorities" are not the same
# strength of evidence as 4-of-5. Matches the worked example above (4
# agreeing samples) and the brief's explicit "90/90/90/9/90" /
# "98/98/98/98/9" scenarios.
BURST_RECOVERY_MIN_AGREEING_SAMPLES = 3

# The relaxed confidence floor for the recovery tier ONLY -- reused
# verbatim from app.validation.temporal, never independently invented or
# tuned for this feature. See the module-level comment block above for why
# this is safe: it is gated behind BURST_SUPERMAJORITY_FRACTION and
# BURST_RECOVERY_MIN_AGREEING_SAMPLES, not applied unconditionally.
BURST_RECOVERY_CONFIDENCE_FLOOR = CONFIDENCE_TEMPORAL_FLOOR

# The confidence floor the AGREEING samples' mean must clear. Deliberately
# CONFIDENCE_MEDIUM_MIN (70) -- the same, already-shipped bar the live path
# already gates single-tick acceptance on (app.validation.rules) -- not a
# new number invented for this feature.
#
# app.validation.temporal.CONFIDENCE_TEMPORAL_FLOOR (40.0) was measured as
# an alternative before settling this (app/eval/
# m5_7_2_burst_verification_eval.py, real Dataset A/B, 274 scored fields at
# 2 noise levels each -- see app/eval/tier2_data/m5_7_2_report/
# m5_7_2_summary.txt for the full numbers). Both floors +
# crop-integrity essentially ELIMINATE confidently-wrong confirmations
# relative to today's single-frame Verify (WRONG 19.7-20.1% single-frame ->
# 0.7-1.1% at floor=70 vs 4.4-4.7% at floor=40) -- but floor=70 is 4-6x
# safer than floor=40 for a ~6-8 percentage point lower stable/success rate
# (44-46% vs 50-54%). Given this project's own stated posture (a false HOLD
# is an acceptable cost; a confidently-wrong CONFIRMATION is not --
# app.validation.crop_integrity's own docstring), the safer floor was kept:
# BURST_CONFIDENCE_FLOOR stays CONFIDENCE_MEDIUM_MIN, not
# CONFIDENCE_TEMPORAL_FLOOR, as a measured decision rather than a default.
BURST_CONFIDENCE_FLOOR = CONFIDENCE_MEDIUM_MIN


@dataclass
class _Sample:
    """One (frame, vital) OCR read -- either preprocessing variant."""

    match_key: object  # float for scalar vitals; (systolic, diastolic) tuple for nibp; None if unreadable/invalid
    confidence: float
    clean: bool  # not has_residual_content -- see module docstring section 2
    raw_text: str
    matched_text: Optional[str]
    original_value: object  # float or NibpValue, exactly as the engine returned it -- what actually gets reported


@dataclass
class FieldBurstResult:
    """Aggregated verdict for one vital across the whole burst. Mirrors the
    single-frame CalibrationVerifyResult's per-field shape (value +
    confidence + diagnostics) so the API layer can still populate that exact
    contract, plus the additive burst metadata the frontend needs to render
    'N of M frames agreed' instead of a bare number."""

    value: object  # float / NibpValue, or None -- None whenever NOT stable, by design (never invent/force a value)
    confidence: float  # mean confidence of the agreeing samples (0 if none)
    raw_text: str
    matched_text: Optional[str]
    agreement_pct: float  # 0-100
    sample_count: int  # total valid samples considered (both variants, if the fallback ran)
    frame_count: int  # frames the burst captured for this field (== len(frames), for context)
    stable: bool
    # True when `stable` was reached via the corroborated-recovery tier
    # (BURST_SUPERMAJORITY_FRACTION agreement at BURST_RECOVERY_CONFIDENCE_FLOOR)
    # rather than the full-confidence tier -- lets the UI say "one noisy
    # frame was discarded" instead of implying every sample was clean and
    # confident. Always False when stable is False.
    recovered: bool
    # Only meaningful when stable is False -- explains WHY, so the UI can
    # distinguish "hold the camera steady and try again" from "the box
    # geometry itself looks wrong, redraw it": one of "no_reading" (no
    # valid samples at all), "low_agreement" (no value reached even a
    # strict majority), "geometry" (a majority was reached but the
    # agreeing samples aren't clean of residual content -- crop-integrity's
    # signal for clipped/truncated digits), "low_confidence" (a majority
    # was reached and is clean, but confidence didn't clear either tier).
    unstable_reason: Optional[str]
    used_variant: bool  # True if the 'adaptive' fallback pass contributed to the final verdict
    best_guess_value: object  # the modal value even when NOT stable, for honest "closest we got" messaging
    best_guess_agreement_pct: float


def _nibp_match_key(v: NibpValue) -> Optional[Tuple[float, float]]:
    """Matches exactly what the UI displays and what an operator can
    actually confirm (CalibrationPage.tsx's formatVerifyValue renders NIBP
    as "sys/dia", never mean) -- a sample missing either half doesn't count
    as a usable read for consensus purposes, same as it can't be confirmed
    today."""
    if v.systolic is None or v.diastolic is None:
        return None
    return (v.systolic, v.diastolic)


def _collect_samples(
    engine: OcrEngine, crops: List[np.ndarray], vital: str, variant: str
) -> List[_Sample]:
    samples: List[_Sample] = []
    for crop in crops:
        value, confidence, diag = engine.read_vital_with_diagnostics(crop, vital, variant=variant)
        clean = not crop_is_suspicious(diag)
        if vital == "nibp":
            match_key = _nibp_match_key(value) if isinstance(value, NibpValue) else None
        else:
            match_key = value
        samples.append(_Sample(
            match_key=match_key, confidence=confidence, clean=clean,
            raw_text=diag.raw_text, matched_text=diag.matched_text, original_value=value,
        ))
    return samples


def _mode(samples: List[_Sample]) -> Tuple[object, List[_Sample]]:
    """Returns (modal match_key, the samples that produced it), ignoring
    invalid (match_key is None) samples. Ties broken by higher mean
    confidence among the tied keys -- deterministic, not the sample order."""
    valid = [s for s in samples if s.match_key is not None]
    if not valid:
        return None, []
    counts = Counter(s.match_key for s in valid)
    top_count = max(counts.values())
    tied_keys = [k for k, c in counts.items() if c == top_count]
    if len(tied_keys) > 1:
        def mean_conf(k: object) -> float:
            group = [s.confidence for s in valid if s.match_key == k]
            return sum(group) / len(group)
        tied_keys.sort(key=mean_conf, reverse=True)
    winner = tied_keys[0]
    winning_samples = [s for s in valid if s.match_key == winner]
    return winner, winning_samples


def _aggregate_field(
    engine: OcrEngine, crops: List[np.ndarray], vital: str, frame_count: int,
    confidence_floor: float = BURST_CONFIDENCE_FLOOR,
) -> FieldBurstResult:
    primary = _collect_samples(engine, crops, vital, variant="otsu")
    stable, recovered, reason, mode_key, mode_samples, all_valid = _evaluate(primary, confidence_floor)

    used_variant = False
    if not stable and crops:
        # Second opinion only, and only when the primary preprocessing
        # couldn't stabilize this field -- bounds the extra OCR cost to
        # exactly the fields that need it (see module docstring section 3).
        secondary = _collect_samples(engine, crops, vital, variant="adaptive")
        combined = primary + secondary
        # Report the COMBINED attempt either way (more samples, a more
        # honest "how close did we get" picture) rather than silently
        # discarding the second opinion's evidence when it doesn't help.
        used_variant = True
        stable, recovered, reason, mode_key, mode_samples, all_valid = _evaluate(combined, confidence_floor)

    total_valid = len(all_valid)
    best_guess_agreement = (len(mode_samples) / total_valid * 100) if total_valid else 0.0

    if stable:
        rep = mode_samples[0]  # any agreeing sample's raw/matched text is representative
        confidence = sum(s.confidence for s in mode_samples) / len(mode_samples)
        return FieldBurstResult(
            value=rep.original_value, confidence=confidence, raw_text=rep.raw_text,
            matched_text=rep.matched_text, agreement_pct=best_guess_agreement,
            sample_count=total_valid, frame_count=frame_count, stable=True,
            recovered=recovered, unstable_reason=None,
            used_variant=used_variant, best_guess_value=rep.original_value,
            best_guess_agreement_pct=best_guess_agreement,
        )

    # Not stable: NEVER report `value` -- "if a stable value cannot be
    # obtained, clearly report that the field could not be verified instead
    # of inventing or forcing a value" (explicit requirement). Still surface
    # the best-guess diagnostics so the UI can explain WHY (low agreement /
    # low confidence / residual content) rather than a bare "-".
    if mode_samples:
        rep = mode_samples[0]
        confidence = sum(s.confidence for s in mode_samples) / len(mode_samples)
        return FieldBurstResult(
            value=None, confidence=confidence, raw_text=rep.raw_text, matched_text=rep.matched_text,
            agreement_pct=best_guess_agreement, sample_count=total_valid, frame_count=frame_count,
            stable=False, recovered=False, unstable_reason=reason,
            used_variant=used_variant, best_guess_value=rep.original_value,
            best_guess_agreement_pct=best_guess_agreement,
        )
    return FieldBurstResult(
        value=None, confidence=0.0, raw_text="", matched_text=None, agreement_pct=0.0,
        sample_count=0, frame_count=frame_count, stable=False, recovered=False,
        unstable_reason=reason, used_variant=used_variant,
        best_guess_value=None, best_guess_agreement_pct=0.0,
    )


def _evaluate(
    samples: List[_Sample], confidence_floor: float = BURST_CONFIDENCE_FLOOR
) -> Tuple[bool, bool, Optional[str], object, List[_Sample], List[_Sample]]:
    """The stability rule (module docstring section 2 + the corroborated-
    recovery tier documented above BURST_SUPERMAJORITY_FRACTION):

      1. A value must be produced by a STRICT MAJORITY of valid samples
         (BURST_AGREEMENT_MIN_FRACTION) even to be considered.
      2. Every agreeing sample must be clean of residual content
         (crop-integrity, M5.4.1) -- checked BEFORE either confidence tier,
         so a systematically truncated/malformed read can never reach
         'stable' via any tier regardless of how often it repeats or how
         confident Tesseract is in the truncated digit(s) it did see.
      3a. FULL-CONFIDENCE TIER: the agreeing group's mean confidence clears
          `confidence_floor` (BURST_CONFIDENCE_FLOOR, 70, by default) --
          stable, not recovered. Same bar as before this hotfix.
      3b. RECOVERY TIER (only reached if 3a fails): agreement is a
          SUPERMAJORITY (BURST_SUPERMAJORITY_FRACTION, e.g. 4-of-5) with at
          least BURST_RECOVERY_MIN_AGREEING_SAMPLES agreeing samples, and
          the group's mean confidence clears BURST_RECOVERY_CONFIDENCE_FLOOR
          (the existing CONFIDENCE_TEMPORAL_FLOOR, 40) -- stable, recovered.
          This is what lets "90, 90, 90, 9, 90" verify as 90: one isolated
          noisy frame does not poison an otherwise strong, clean majority.

    Returns (stable, recovered, unstable_reason, mode_key, mode_samples,
    valid). `unstable_reason` is only meaningful when stable is False; see
    FieldBurstResult.unstable_reason's docstring for the possible values."""
    valid = [s for s in samples if s.match_key is not None]
    mode_key, mode_samples = _mode(samples)
    if mode_key is None or not mode_samples or not valid:
        return False, False, "no_reading", mode_key, mode_samples, valid

    agreement_fraction = len(mode_samples) / len(valid)
    mean_confidence = sum(s.confidence for s in mode_samples) / len(mode_samples)
    all_clean = all(s.clean for s in mode_samples)

    if agreement_fraction <= BURST_AGREEMENT_MIN_FRACTION:
        return False, False, "low_agreement", mode_key, mode_samples, valid

    if not all_clean:
        # A majority was reached, but the agreeing samples themselves carry
        # residual OCR content -- the crop-integrity signature of a clipped
        # or malformed digit run (M5.4.1). Neither confidence tier can ever
        # override this: a systematic truncation that repeats (e.g. 83 ->
        # "8" every time) must not become trusted just because it repeats.
        return False, False, "geometry", mode_key, mode_samples, valid

    if mean_confidence >= confidence_floor:
        return True, False, None, mode_key, mode_samples, valid

    if (
        agreement_fraction >= BURST_SUPERMAJORITY_FRACTION
        and len(mode_samples) >= BURST_RECOVERY_MIN_AGREEING_SAMPLES
        and mean_confidence >= BURST_RECOVERY_CONFIDENCE_FLOOR
    ):
        return True, True, None, mode_key, mode_samples, valid

    return False, False, "low_confidence", mode_key, mode_samples, valid


def collect_all_samples(
    engine: OcrEngine, crops: List[np.ndarray], vital: str
) -> Tuple[List[_Sample], List[_Sample]]:
    """Returns (primary_otsu_samples, secondary_adaptive_samples) -- BOTH
    variants, always, regardless of whether the primary pass alone would
    have stabilized. Production's _aggregate_field never calls this (it
    only pays for the adaptive pass when the primary one needs help, see
    its own docstring) -- this exists for app/eval/
    m5_7_2_burst_verification_eval.py, which needs both variants' evidence
    computed ONCE so it can compare multiple confidence-floor choices
    against the SAME OCR results, instead of re-running Tesseract per floor
    tested."""
    primary = _collect_samples(engine, crops, vital, variant="otsu")
    secondary = _collect_samples(engine, crops, vital, variant="adaptive")
    return primary, secondary


def verify_burst(
    frames: List[np.ndarray],
    roi_boxes: Dict[str, NormalizedBox],
    engine: Optional[OcrEngine] = None,
    confidence_floor: float = BURST_CONFIDENCE_FLOOR,
) -> Dict[str, FieldBurstResult]:
    """Runs burst verification for every vital with a drawn box, across all
    `frames`. Returns {vital: FieldBurstResult} -- one entry per key present
    in roi_boxes (matching extract_rois_from_boxes/read_frame's own "only
    look at drawn/known vitals" contract), in VITALS order.

    `frames` should be several REAL, independently-captured frames of the
    same static setup (spaced far enough apart to be genuinely different
    camera reads, not the identical buffer sampled twice) -- see
    app.api.calibration's verify_burst_candidate endpoint, which is the only
    production caller (and which never overrides confidence_floor -- that
    parameter exists so app/eval/m5_7_2_burst_verification_eval.py can
    measure alternate floors against real data without a second copy of
    this function)."""
    engine = engine or get_default_engine()
    results: Dict[str, FieldBurstResult] = {}
    for vital in VITALS:
        box = roi_boxes.get(vital)
        if box is None:
            continue
        crops: List[np.ndarray] = []
        for frame in frames:
            rois = extract_rois_from_boxes(frame, {vital: box})
            roi_result = rois.get(vital)
            if roi_result is not None:
                crops.append(roi_result.crop)
        results[vital] = _aggregate_field(
            engine, crops, vital, frame_count=len(frames), confidence_floor=confidence_floor
        )
    return results
