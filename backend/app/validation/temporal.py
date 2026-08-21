"""M5.4: temporal corroboration -- an additional, narrowly-scoped signal
reconcile() may use to confirm a value whose OCR confidence alone falls
below CONFIDENCE_MEDIUM_MIN, when the SAME parsed value has been read on
several consecutive ticks. See docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md
for the measurement this design is built from (app.eval.
m5_4_signal_predictiveness) and why the two constants below sit where they
do.

WHAT THIS IS NOT. It does not replace OCR confidence, range checking, or
jump checking -- reconcile() runs all three, in the same order, before this
is ever consulted (see reconcile.py's per-field loop). It can only ADD one
more path to confirmation for a value that has ALREADY passed range/jump
and independently FAILED the existing confidence gate; it can never
override a rejection, lower CONFIDENCE_MEDIUM_MIN, or touch a value the
confidence gate already accepts or rejects for its own reasons.

STATE. Per (WS connection, field): the last raw OCR value observed and how
many consecutive ticks it has repeated. Two numbers per field -- bounded,
O(1) memory per field, reset whenever the run breaks (a missing/withheld
read, or a different value) and whenever a fresh connection calls
initial_temporal_state(). Never persisted, never shared across sessions;
owned by app.ws.vitals.send_loop exactly like confirmed_state and
TrackingState already are (see that module) -- the same per-connection
lifetime, the same "never survives past this WebSocket" contract.

WHY BAD TRACKING CANNOT INCREASE TRUST HERE. A tracking failure (or any
other extraction failure) makes app.pipeline.calibrated_roi withhold the
field for that tick, which reaches this module as raw_value=None -- observe()
resets the run to zero on exactly that input. There is no separate
"tracking status" input to this module at all: an unreadable tick already
breaks the run by construction, so a tracker that starts failing cannot
quietly keep a stale run alive.

M5.4.1: WHY A SUSPICIOUS CROP CANNOT INCREASE TRUST HERE EITHER. The
mechanism above is exactly what let a SYSTEMATICALLY truncated crop defeat
it: a too-narrow calibrated/tracked HR box read "83"/"84" as "8" on every
tick, so the same wrong value repeating looked identical to genuine
repeated signal (docs/M5_4_1_CROP_INTEGRITY_REPORT.md). observe() now also
takes `crop_suspicious` (app.validation.crop_integrity.has_residual_content
-- did the OCR engine's raw text contain content beyond the parsed value,
e.g. "8g" for a value of 8) and is_corroborated() additionally requires
the ENTIRE current run be clean, not just the current tick: one suspicious
tick invalidates corroboration for every tick in that run, current and
past, until a fresh (non-repeating or missing) value restarts it. This is
deliberately the conservative direction -- see TemporalFieldState.clean_run
and is_corroborated() below.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from app.validation.rules import FIELDS

# Evidence for both constants below: docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md
# Phase 1, produced by app/eval/m5_4_signal_predictiveness.py against real
# Dataset A, frozen Dataset B, and the dense_B 270-frame temporal recording.

# Below this, M5.1's own oracle-crop confidence-bucket measurement
# (docs/EVIDENCE.md sec 5.1: Dataset A conf<40 -> 5% accurate on n=21,
# Dataset B conf<40 -> 30% accurate on n=60) already showed OCR confidence
# carries almost no signal at all in that band. Repeating a near-zero-
# confidence misread is not corroborating evidence -- it is the same
# unreliable engine failing the same way twice. Temporal corroboration
# therefore still requires a non-trivial confidence floor from the OCR
# engine itself; it never substitutes for OCR confidence entirely.
CONFIDENCE_TEMPORAL_FLOOR = 40.0

# Consecutive ticks (same parsed value, at or above the floor above)
# required before corroboration can fire.
#
# *** THIS CONSTANT DOES NOT MAKE THE MECHANISM SAFE. IT IS SHIPPED OFF BY
# DEFAULT (app.ws.vitals._temporal_corroboration_enabled) AND SHOULD STAY
# OFF. *** Phase 1's discovery-sample measurement (frozen Dataset A, ONE
# Dataset B reference frame, the dense_B recording) found zero sub-gate
# repeated-and-wrong cases and this constant was set from that -- but
# Phase 6's held-out validation (replaying the SAME mechanism against
# Dataset B's SECOND reference frame, frozen_B[sample_0011], which
# docs/M5_3_LAYOUT_TRACKING_REPORT.md sec 9 already flags as the harder
# calibration arm) found a real counter-example: HR truncated "83"/"84" ->
# "8" (a box-localization clipping artifact, the SAME failure class as
# Dataset A's own known confidently-wrong HR/SpO2 cases, sec 3 of that
# report) repeats identically at confidence 58 and 66 -- INSIDE this
# module's [CONFIDENCE_TEMPORAL_FLOOR, CONFIDENCE_MEDIUM_MIN) band -- and
# gets wrongly corroborated. No confidence floor in that band cleanly
# separates this case from the genuine corroborations measured in the same
# arm (correct SpO2 reads sit at confidence 44-58.5, overlapping the wrong
# HR reads' 58-66 almost entirely) -- see
# docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md Phase 6 for the full
# reconstruction (app/eval/m5_4_confirmation_eval.py).
#
# ROOT CAUSE: a SYSTEMATIC (correlated) OCR failure -- the same clipped crop
# read the same wrong way every tick -- looks IDENTICAL to genuine repeated
# signal from this module's point of view, because run-length only measures
# "did the same value repeat", never "is this repetition independent
# evidence". Repetition is a safe corroborator only against independent,
# per-tick noise; it is not a safe corroborator against a bug that recurs
# deterministically. This pipeline has now produced that exact failure mode
# twice (Dataset A's HR/SpO2 truncation, Dataset B[sample_0011]'s HR
# truncation) -- it is a real, recurring risk, not a one-off.
#
# run>=3 is kept as the value the (still real, still evidence-backed)
# discovery-sample measurement supports.
#
# M5.4.1 UPDATE: the independent signal M5.4 named as missing here now
# exists -- app.validation.crop_integrity.has_residual_content, consulted
# by is_corroborated() via TemporalFieldState.clean_run below. Replaying
# the EXACT frozen_B[sample_0011] HR failure that motivated the warning
# above (docs/M5_4_1_CROP_INTEGRITY_REPORT.md) confirms it is now refused:
# the "8g"/"8B" raw OCR text on every one of those ticks flips clean_run to
# False, so is_corroborated() returns False throughout, and the value stays
# held instead of confirmed. TEMPORAL_CORROBORATION is still shipped OFF by
# default (app.ws.vitals._temporal_corroboration_enabled) -- not because
# this specific failure mode is unresolved, but because M5.4's OTHER open
# item (docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md sec 12, item 3: this
# project's entire held-out evidence base remains one 54-second recording
# of one monitor) is unchanged by this milestone. See
# docs/M5_4_1_CROP_INTEGRITY_REPORT.md for the full before/after validation
# and the recommendation this leaves for whoever revisits the default.
TEMPORAL_AGREEMENT_MIN_RUN = 3


@dataclass(frozen=True)
class TemporalFieldState:
    last_value: Optional[float] = None
    run_length: int = 0
    # M5.4.1: True as long as EVERY tick in the current run had
    # crop_suspicious=False (app.validation.crop_integrity.
    # has_residual_content). A single suspicious tick flips this to False
    # for the rest of the run -- it can never flip back to True except by
    # the run itself resetting (a new value, or a missing read). See
    # is_corroborated() and the module docstring's "M5.4.1" section.
    clean_run: bool = True


def initial_temporal_state() -> Dict[str, TemporalFieldState]:
    """Fresh, empty state -- call once per new WS connection, same lifetime
    as app.validation.reconcile.initial_confirmed_state(). Never reuse
    across sessions or connections (session-isolation requirement)."""
    return {field: TemporalFieldState() for field in FIELDS}


def observe(
    state: TemporalFieldState, raw_value: Optional[float], crop_suspicious: bool = False
) -> TemporalFieldState:
    """Advances one field's run-length state by one tick's raw OCR value --
    BEFORE any range/jump/confidence gating, so this tracks what OCR
    physically read, not what reconcile() went on to decide. A missing read
    (raw_value is None, meaning extraction withheld the field or OCR
    returned nothing) breaks the run -- matching both this milestone's
    measurement methodology (app.eval.m5_4_signal_predictiveness.
    chronological_agreement resets identically on a missing read) and the
    safety requirement that a tracking/extraction failure can never leave a
    stale run available to corroborate a later tick.

    crop_suspicious (M5.4.1, optional, default False -- so every pre-M5.4.1
    call site, and any caller that cannot derive this evidence, is
    unaffected): this tick's app.validation.crop_integrity.
    has_residual_content result. ANDed into clean_run cumulatively across
    the run -- one suspicious tick taints the whole run for corroboration
    purposes, because the failure this guards against (a too-narrow
    calibrated/tracked box) is a SYSTEMATIC defect that repeats every tick,
    not independent per-tick noise; a run that ever showed the symptom is
    not trustworthy evidence even on the ticks where it happened not to
    show it."""
    if raw_value is None:
        return TemporalFieldState(last_value=None, run_length=0, clean_run=True)
    if state.last_value is not None and raw_value == state.last_value:
        return TemporalFieldState(
            last_value=raw_value,
            run_length=state.run_length + 1,
            clean_run=state.clean_run and not crop_suspicious,
        )
    return TemporalFieldState(last_value=raw_value, run_length=1, clean_run=not crop_suspicious)


def is_corroborated(state: TemporalFieldState, confidence: float) -> bool:
    """True when THIS tick's evidence -- OCR confidence, how many
    consecutive prior+current ticks agree on the same value, AND (M5.4.1)
    whether any of those ticks showed OCR evidence of a suspicious/
    truncated crop -- clears the corroboration bar. Callers (reconcile())
    must only consult this for a reading that has ALREADY passed range and
    jump checks and independently FAILED the CONFIDENCE_MEDIUM_MIN gate --
    see the module docstring; this function has no way to enforce that
    itself, it only answers the confidence+repetition+integrity question."""
    return (
        confidence >= CONFIDENCE_TEMPORAL_FLOOR
        and state.run_length >= TEMPORAL_AGREEMENT_MIN_RUN
        and state.clean_run
    )
