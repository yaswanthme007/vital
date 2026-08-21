"""M5.8: the LIVE camera path's acceptance rule -- a value becomes a
confirmed observation only once several independently-captured frames agree
on it, never off one frame.

============================================================================
WHY THE SINGLE-TICK CONFIDENCE GATE WAS NOT ENOUGH (measured, not assumed)
============================================================================

Before this module the live path confirmed a field the moment ONE frame's
OCR confidence cleared CONFIDENCE_MEDIUM_MIN (70). Against a real webcam
pointed at a real anaesthesia monitor that rule is close to a coin flip,
because Tesseract's per-frame confidence on a photographed 7-segment
display is itself noisy: in the demo recording preserved in the project's
own vital.db (session SESSION-1787247886294-cv6z, 62 persisted rows over
~4 minutes, monitor physically displaying a steady SpO2 = 98) the HR crop's
confidence wandered between 0 and 76 frame to frame WITHOUT the monitor
changing at all, and the rows that happened to clear 70 recorded SpO2 as
92, 94, 96, 97 and 99 -- five values that were never on the screen -- plus
EtCO2 = 4 and RR = 42. Every one of those became a permanent row in the
patient's observed timeline.

Raising the confidence bar does not fix that (the wrong reads were often
the CONFIDENT ones -- 83% for EtCO2 = 4), and lowering it obviously does
not either. The missing ingredient is not a better threshold on one frame;
it is more than one frame.

============================================================================
THE RULE
============================================================================

Per field, a bounded window of the last WINDOW_SIZE OCR reads is kept
(value, confidence, and whether the crop looked clean). A value is accepted
only when ALL of:

  1. it is what THIS tick read -- a majority left over from before the
     monitor changed can never confirm itself once the display moves on;
  2. it was read on at least N of the frames in the window (N=2 for the
     full-confidence tier, N=3 for the corroborated-recovery tier);
  3. EVERY agreeing sample was clean of residual OCR content
     (app.validation.crop_integrity) -- unconditional, evaluated before
     either confidence tier, exactly as app.pipeline.burst_verify does it;
  4. the agreeing samples' MEAN confidence clears that tier's floor.

Two tiers, deliberately the same shape as the calibration-side burst
verifier (app.pipeline.burst_verify._evaluate) so calibration and live
observation judge evidence the same way rather than drifting apart:

  Tier A -- 2 agreeing samples, mean confidence >= CONFIDENCE_MEDIUM_MIN
            (70, the existing live-path bar, unchanged in value).
  Tier B -- 3 agreeing samples, mean confidence >= CONFIDENCE_TEMPORAL_FLOOR
            (40, the existing constant app.validation.temporal already
            validated for the "several corroborating reads justify a lower
            per-read confidence than one untrusted tick would need" role).

Neither constant is invented here; what is new is that NEITHER tier can be
reached by a single frame.

============================================================================
WHAT THIS DELIBERATELY DOES *NOT* DO
============================================================================

**It does not over-stabilize.** Agreement is counted for the CURRENT tick's
value only, and old samples age out of the window -- so when the monitor
genuinely changes 88 -> 90, the two 90s that follow confirm 90 (a few
seconds at the camera's ~1 Hz tick), rather than the window's earlier 88s
holding the display hostage. A value that genuinely oscillates every frame
(89, 90, 91, 88) simply keeps the last confirmed value on screen and adds
no observation, which is the honest outcome: nothing was read consistently
enough to be worth writing down.

**It does not replace any existing check.** reconcile() still applies
physiological range bounds and jump limits BEFORE consulting this module,
and app.pipeline.calibrated_roi still withholds every field on a tracking
failure. This can only ever make acceptance harder than the pre-M5.8 gate,
never easier: every value it accepts would also have been accepted by the
old rule at some point, but not every value the old rule accepted survives
this one.

**It is not simple repetition.** Repetition alone has a known blind spot
this project has been bitten by twice: a systematically bad crop (a box
clipping a digit) reproduces the SAME wrong value every frame, which looks
identical to agreement. Requirement 3 above is the defense -- the same
crop-integrity signal, applied the same unconditional way, that
app.validation.temporal and app.pipeline.burst_verify already use.
"""

from collections import Counter
from dataclasses import dataclass, field as dataclass_field
from typing import Deque, Dict, List, Optional, Tuple
from collections import deque

from app.validation.rules import CONFIDENCE_MEDIUM_MIN, FIELDS
from app.validation.temporal import CONFIDENCE_TEMPORAL_FLOOR

# How many recent reads are retained per field. 5 mirrors
# app.pipeline.burst_verify's BURST_FRAME_COUNT: the live path is a rolling
# burst, and the two should not disagree about how much evidence "enough"
# is. Bounded by construction -- 5 small tuples per field, per WS
# connection, for a case of any length.
WINDOW_SIZE = 5

# Tier A: the smallest number of independent frames that is meaningfully
# more than one. Two agreeing high-confidence reads of the same digits is
# the evidence a single 70%-confident frame was standing in for.
AGREEMENT_MIN_SAMPLES = 2

# Tier B: more frames required, in exchange for a lower per-frame
# confidence bar -- the same trade app.pipeline.burst_verify's recovery
# tier makes, for the same reason (real webcam OCR confidence on a correct
# read routinely sits in the 45-65 band).
RECOVERY_MIN_SAMPLES = 3

CONFIDENCE_FLOOR = CONFIDENCE_MEDIUM_MIN
RECOVERY_CONFIDENCE_FLOOR = CONFIDENCE_TEMPORAL_FLOOR


@dataclass(frozen=True)
class Observation:
    """One field's OCR read on one processed frame."""

    value: Optional[float]
    confidence: float
    clean: bool


@dataclass
class FieldEvidence:
    """The rolling window for one field. Owned by the caller (one dict per
    WebSocket connection, same lifetime as reconcile()'s last_confirmed and
    app.ws.vitals' TrackingState) and mutated in place -- never persisted,
    never shared between sessions."""

    samples: Deque[Observation] = dataclass_field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))

    def observe(self, value: Optional[float], confidence: float, crop_suspicious: bool) -> None:
        """Records this tick's raw OCR read, BEFORE any range/jump/confidence
        gating -- this window tracks what the camera physically read, not
        what reconcile() decided. An unreadable tick (value is None) is
        recorded too rather than skipped: it is real evidence that the field
        is not being read consistently, and letting it occupy a slot is what
        stops two agreeing reads six unreadable frames apart from counting
        as corroboration."""
        self.samples.append(Observation(value=value, confidence=confidence, clean=not crop_suspicious))


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    # 'corroborated' | 'corroborated_recovery' when accepted; otherwise
    # 'awaiting_corroboration' (not enough agreeing frames yet),
    # 'geometry' (agreeing frames exist but crop integrity flagged them),
    # or 'low_confidence' (enough clean agreement, neither floor cleared).
    reason: str
    agreeing_samples: int
    mean_confidence: float


def initial_evidence_state() -> Dict[str, FieldEvidence]:
    """Fresh per-connection state -- call once, exactly like
    app.validation.reconcile.initial_confirmed_state()."""
    return {field: FieldEvidence() for field in FIELDS}


def _agreeing(evidence: FieldEvidence, value: float) -> List[Observation]:
    return [s for s in evidence.samples if s.value is not None and s.value == value]


def evaluate(evidence: FieldEvidence, current_value: Optional[float]) -> Verdict:
    """Decides whether `current_value` -- the value THIS tick's OCR read,
    already past reconcile()'s range and jump checks -- has enough
    corroborating evidence in the window to become a confirmed observation.

    The caller must have already called evidence.observe() for this tick, so
    the current read is itself one of the agreeing samples."""
    if current_value is None:
        return Verdict(False, "awaiting_corroboration", 0, 0.0)

    agreeing = _agreeing(evidence, current_value)
    count = len(agreeing)
    mean_confidence = (sum(s.confidence for s in agreeing) / count) if count else 0.0

    if count < AGREEMENT_MIN_SAMPLES:
        return Verdict(False, "awaiting_corroboration", count, mean_confidence)

    # Crop integrity first and unconditionally -- a systematically clipped
    # box can never reach either tier no matter how many frames agree or how
    # confident they are. Same ordering as burst_verify._evaluate.
    if not all(s.clean for s in agreeing):
        return Verdict(False, "geometry", count, mean_confidence)

    if count >= AGREEMENT_MIN_SAMPLES and mean_confidence >= CONFIDENCE_FLOOR:
        return Verdict(True, "corroborated", count, mean_confidence)
    if count >= RECOVERY_MIN_SAMPLES and mean_confidence >= RECOVERY_CONFIDENCE_FLOOR:
        return Verdict(True, "corroborated_recovery", count, mean_confidence)
    return Verdict(False, "low_confidence", count, mean_confidence)


def modal_value(evidence: FieldEvidence) -> Tuple[Optional[float], int]:
    """The most-read value in the window and how many samples produced it --
    diagnostics only (flagged-entry notes / debugging). Never an acceptance
    input: `evaluate` deliberately judges the CURRENT tick's value, not the
    window's mode, so a stale majority can't confirm itself."""
    values = [s.value for s in evidence.samples if s.value is not None]
    if not values:
        return None, 0
    value, count = Counter(values).most_common(1)[0]
    return value, count
