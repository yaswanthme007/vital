from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.validation import live_corroboration
from app.validation import temporal as temporal_signal
from app.validation.rules import (
    CONFIDENCE_MEDIUM_MIN,
    FIELD_TO_VITAL_GROUP,
    FIELDS,
    JUMP_LIMITS,
    RANGE_BOUNDS,
    confidence_tier,
    is_in_range,
    is_jump_rejected,
    normalize_temp_celsius,
)

JUMP_LIMITS_WINDOW = {field: window for field, (_, window) in JUMP_LIMITS.items()}

# Groups the 8 numeric fields into the 6 vitals a FlaggedReading is reported
# against — NIBP's 3 sub-fields collapse into ONE flagged entry (matching
# the frontend's own mock data, which flags NIBP as one "165/100" -> "128/82"
# entry, not three separate ones). Order within the nibp group matters: it's
# [systolic, diastolic, mean] and only systolic/diastolic are shown in the
# "sys/dia" display string (mean is still independently held/accepted
# underneath, it just has no display slot in this string format).
VITAL_GROUPS: Dict[str, List[str]] = {
    "hr": ["hr"],
    "spo2": ["spo2"],
    "etco2": ["etco2"],
    "temp": ["temp"],
    "rr": ["rr"],
    "nibp": ["nibpSystolic", "nibpDiastolic", "nibpMean"],
}

UNITS: Dict[str, str] = {
    "hr": "bpm",
    "spo2": "%",
    "nibpSystolic": "mmHg",
    "nibpDiastolic": "mmHg",
    "nibpMean": "mmHg",
    "etco2": "mmHg",
    "temp": "°C",
    "rr": "/min",
}

# Seeded as the initial "last confirmed" state for a fresh connection so
# hold-last-confirmed always has something safe to fall back on, even before
# any real reading has been confirmed. Clinically-normal placeholders only —
# if one is ever actually surfaced (i.e. tick 1's own read also fails
# validation), it is ALWAYS flagged as an unconfirmed baseline, never
# presented as a real read.
DEFAULT_BASELINE: Dict[str, float] = {
    "hr": 75,
    "spo2": 98,
    "nibpSystolic": 120,
    "nibpDiastolic": 78,
    "nibpMean": 92,
    "etco2": 38,
    "temp": 36.8,
    "rr": 14,
}

_CRITICAL_REASONS = {"implausible_range", "jump_rejected"}

# M5.8: reasons that describe the pipeline's ORDINARY resting state on a
# live camera -- "this frame didn't read", "not enough agreeing frames yet",
# "nothing confirmed so far" -- rather than an anomaly a human should look
# at. On a real webcam these fire on most fields on most ticks (the demo
# recording produced 4,374 FlaggedReading rows in four minutes, essentially
# all of them "OCR confidence below threshold" and "could not read a value
# this tick"), which buried the handful of genuinely reviewable events and
# made Archive's flagged count meaningless. They are suppressed ONLY on the
# corroborated (camera) path -- see reconcile()'s flagged-assembly loop --
# so every non-camera call site keeps its exact pre-M5.8 flagged output.
_ROUTINE_HOLD_REASONS = {"unreadable", "low_confidence", "awaiting_corroboration", "unconfirmed"}

_REASON_TEXT = {
    "unreadable": "OCR could not read a value this tick",
    "implausible_range": "{value} is outside the physiologically plausible range {bounds}",
    "jump_rejected": "{value} is an implausible jump from the last confirmed {last_value} within {window:g}s",
    "low_confidence": "OCR confidence {confidence:.0f}% is below the ai_low threshold ({tier_min:.0f}%)",
    "medium_confidence": "OCR confidence {confidence:.0f}% is ai_medium ({tier_min:.0f}-89%) — accepted but flagged for review",
    "temporal_corroboration": (
        "OCR confidence {confidence:.0f}% is below the {tier_min:.0f}% threshold, but the same value was "
        "read on {run_length} consecutive ticks — accepted via temporal corroboration, flagged for review"
    ),
    "baseline_unconfirmed": "no confirmed reading yet — holding the pre-session baseline value",
    # M5.8 live-corroboration outcomes (camera path only -- see the
    # `corroboration` parameter on reconcile()).
    "awaiting_corroboration": (
        "{value} was read this tick but not yet on enough recent frames to confirm — holding"
    ),
    "corroborated": (
        "{value} was read on {run_length} of the last frames at {confidence:.0f}% confidence — confirmed"
    ),
    "corroborated_recovery": (
        "{value} was read on {run_length} of the last frames — confirmed via multi-frame corroboration "
        "at {confidence:.0f}% mean confidence, flagged for review"
    ),
    "crop_geometry": (
        "{value} repeated across frames but the OCR text around it looks clipped — the ROI box may not "
        "contain the whole reading; holding rather than confirming"
    ),
    "unconfirmed": "no confirmed reading yet — nothing is displayed until the camera reads one",
}


@dataclass
class FieldState:
    value: float
    timestamp: int


def initial_confirmed_state(timestamp_ms: int) -> Dict[str, FieldState]:
    return {field: FieldState(value=DEFAULT_BASELINE[field], timestamp=timestamp_ms) for field in FIELDS}


def _fmt_number(value: Optional[float]) -> str:
    if value is None:
        return "?"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _format_group_value(group_fields: List[str], values: Dict[str, Optional[float]]) -> str:
    if len(group_fields) == 1:
        return _fmt_number(values[group_fields[0]])
    systolic, diastolic = group_fields[0], group_fields[1]
    return f"{_fmt_number(values[systolic])}/{_fmt_number(values[diastolic])}"


def _reason_note(
    field: str, reason: str, value: Optional[float], last_value: Optional[float], confidence: float,
    run_length: Optional[int] = None,
) -> str:
    bounds = RANGE_BOUNDS.get(field)
    window = JUMP_LIMITS_WINDOW.get(field)
    text = _REASON_TEXT[reason].format(
        value=_fmt_number(value),
        last_value=_fmt_number(last_value),
        bounds=bounds,
        window=window or 0.0,
        confidence=confidence,
        tier_min=CONFIDENCE_MEDIUM_MIN,
        run_length=run_length or 0,
    )
    return f"{field}: {text}"


def reconcile(
    raw_reading: dict,
    per_vital_confidence: Optional[Dict[str, float]],
    last_confirmed: Dict[str, FieldState],
    temporal_state: Optional[Dict[str, temporal_signal.TemporalFieldState]] = None,
    per_vital_crop_suspicious: Optional[Dict[str, bool]] = None,
    field_status: Optional[Dict[str, str]] = None,
    corroboration: Optional[Dict[str, live_corroboration.FieldEvidence]] = None,
    allow_baseline: bool = True,
) -> Tuple[dict, Dict[str, FieldState], List[dict]]:
    """Turn one raw OCR/synthetic tick into a trustworthy, ALWAYS-COMPLETE
    reading (deterministic — no LLM, pure aside from timestamps it's handed).

    Returns (reading, updated_last_confirmed, flagged):
      reading: VitalReading-shaped dict, all 8 numeric fields non-null.
      updated_last_confirmed: pass this into the NEXT call. Only fields that
        were genuinely ACCEPTED this tick advance; held/rejected fields keep
        their prior confirmed value+timestamp unchanged.
      flagged: FlaggedReading-shaped dicts (S0 model shape, status="pending",
        no "id" — the persistence layer assigns that) for anything
        ai_medium / ai_low / rejected / unreadable this tick. NIBP's 3
        fields collapse into at most one combined entry.

    temporal_state (M5.4, optional, default None): a per-connection
    Dict[field, app.validation.temporal.TemporalFieldState] the CALLER owns
    and passes back in every tick (same pattern as last_confirmed, except
    this one IS mutated in place — see app.validation.temporal's module
    docstring for why an in-place dict was chosen over a fourth return
    value: every existing call site of reconcile() unpacks exactly 3 return
    values, and this keeps every one of them byte-for-byte unaffected).
    When None (the default — every pre-M5.4 call site, and every call site
    that never opts in), the temporal-corroboration branch below never
    executes and behaviour is IDENTICAL to pre-M5.4 reconcile(): a field
    below CONFIDENCE_MEDIUM_MIN is held, full stop. app.ws.vitals only ever
    passes a real dict here when TEMPORAL_CORROBORATION=on is explicitly
    set — see app.validation.temporal's TEMPORAL_AGREEMENT_MIN_RUN comment
    and docs/M5_4_1_CROP_INTEGRITY_REPORT.md for the current status.

    per_vital_crop_suspicious (M5.4.1, optional, default None): a
    {vital_group: bool} dict from app.pipeline.read_frame's own
    `crop_integrity` output (app.validation.crop_integrity.
    has_residual_content per vital), passed straight through to
    temporal_signal.observe() so a systematically truncated crop can never
    accumulate a trustworthy corroborating run — see
    app.validation.temporal's module docstring. None (the default) means
    every field is treated as not-suspicious, i.e. byte-for-byte pre-M5.4.1
    behaviour of the temporal-corroboration branch itself; this can only
    ever make that branch MORE willing to fire, never less, so a caller
    that omits this argument gets exactly M5.4's original (off-by-default)
    behaviour, not a silently safer one.

    field_status (M5.7, optional, default None): an OUT-param dict the
    CALLER owns and this function fills in-place with one of
    'confirmed' | 'held' | 'baseline' per field — the same in-place-output
    pattern temporal_state and crop_integrity already use, chosen for the
    same reason: every existing call site unpacks exactly 3 return values,
    and adding a 4th would break all of them. 'confirmed' means this tick's
    raw_value passed range/jump/confidence (or temporal corroboration) and
    genuinely became the new last_confirmed value — the only status a
    caller should ever treat as a fresh, timestamped OBSERVATION worth
    persisting to the patient's history. 'held' means final_value is the
    PRIOR confirmed value, unchanged this tick (raw_value was missing,
    rejected, or below the confidence gate) — real for display (avoids UI
    flicker) but not a new observation. 'baseline' means there was no prior
    confirmed value at all and DEFAULT_BASELINE (or this tick's own
    raw_value, if present but rejected) is being shown as a last resort —
    this must never be recorded as a confirmed vital sign. 'unknown' (M5.8,
    only reachable when allow_baseline=False) means the field has NEVER been
    confirmed and this tick did not confirm it either -- final_value is None
    and the UI must show nothing at all. None (the default) skips this
    bookkeeping entirely; every pre-M5.7 call site is therefore untouched and
    reconcile()'s return value is byte-for-byte identical to before this
    parameter existed.

    corroboration (M5.8, optional, default None): a per-connection
    Dict[field, app.validation.live_corroboration.FieldEvidence] the CALLER
    owns and this function mutates in place (same pattern as temporal_state).
    When supplied, it REPLACES the single-tick confidence tier as the
    acceptance rule: a value must additionally be reproduced by several
    recent frames, all clean of residual OCR content, before it can be
    confirmed -- see that module's docstring for the measured reason (the
    demo recording's own SpO2 92/94/96/97/99, EtCO2 4 and RR 42 rows were
    all single frames that happened to clear 70% confidence). Range and jump
    checks still run first and are unchanged; this can only make acceptance
    STRICTER, never looser. When None (every non-camera call site), the
    pre-M5.8 confidence-tier logic below runs exactly as before.

    allow_baseline (M5.8, optional, default True): when False, a field with
    no prior confirmed value that this tick did not confirm reports
    final_value None with field_status 'unknown', and nothing is written into
    updated_last_confirmed for it -- instead of falling back to
    DEFAULT_BASELINE. This closes the defect that motivated M5.8: the camera
    path seeded every field with a clinically-normal placeholder (HR 75,
    Temp 36.8, RR 14) at connection time, which the very next tick then
    re-labelled 'held' because a prior value now existed -- so a fabricated
    number was displayed as "Held · last confirmed 22:51:42" for the whole
    case, indistinguishable from a real reading the camera had genuinely
    seen. True (the default) keeps the pre-M5.8 baseline behaviour for
    synthetic/replay sources, whose baseline IS a legitimate starting point
    because they are not claiming to observe a physical monitor.
    """
    per_vital_confidence = per_vital_confidence or {}
    now_ms = raw_reading.get("timestamp")

    reading: dict = {"timestamp": raw_reading.get("timestamp")}
    new_confirmed: Dict[str, FieldState] = dict(last_confirmed)
    # Per-field bookkeeping used to build grouped (NIBP) flags after the
    # per-field accept/hold decisions are all made.
    field_reason: Dict[str, Optional[str]] = {}
    field_confidence: Dict[str, float] = {}
    field_run_length: Dict[str, Optional[int]] = {}
    raw_values: Dict[str, Optional[float]] = {}
    final_values: Dict[str, Optional[float]] = {}

    for field in FIELDS:
        vital_group = FIELD_TO_VITAL_GROUP[field]
        confidence = per_vital_confidence.get(vital_group, 0.0)
        raw_value = raw_reading.get(field)
        if field == "temp" and raw_value is not None:
            # Unit-normalize BEFORE any validation -- see rules.py's
            # normalize_temp_celsius docstring. Converted once here, so
            # everything downstream (range/jump checks, the confirmed
            # value, the flagged aiValue/suggestedValue display strings)
            # sees one consistent unit, matching UNITS["temp"] == "°C".
            raw_value = normalize_temp_celsius(raw_value)
        prior = last_confirmed.get(field)
        prior_value = prior.value if prior else None
        prior_ts = prior.timestamp if prior else None
        elapsed_seconds = ((now_ms - prior_ts) / 1000.0) if (prior_ts is not None and now_ms is not None) else None

        reason: Optional[str] = None
        accept = False
        run_length: Optional[int] = None

        # M5.4: advance the per-field agreement-run state on EVERY tick that
        # has a raw value, regardless of what range/jump/confidence go on to
        # decide -- this tracks what OCR physically read, not what got
        # confirmed (see app.validation.temporal.observe's docstring for why
        # a missing/withheld read resets it). Only ever active when the
        # caller opted in by passing a temporal_state dict; otherwise this is
        # a no-op and the rest of this function is untouched.
        if temporal_state is not None:
            crop_suspicious = (per_vital_crop_suspicious or {}).get(vital_group, False)
            field_state = temporal_signal.observe(
                temporal_state.get(field, temporal_signal.TemporalFieldState()), raw_value, crop_suspicious
            )
            temporal_state[field] = field_state
            run_length = field_state.run_length

        # M5.8: the live-camera evidence window advances on EVERY tick that
        # has a raw value, before any gating -- like temporal_state above, it
        # records what the camera physically read, not what was confirmed.
        evidence = None
        if corroboration is not None:
            evidence = corroboration.setdefault(field, live_corroboration.FieldEvidence())
            evidence.observe(
                raw_value, confidence, (per_vital_crop_suspicious or {}).get(vital_group, False)
            )

        if raw_value is None:
            reason = "unreadable"
        elif not is_in_range(field, raw_value):
            reason = "implausible_range"
        elif is_jump_rejected(field, raw_value, prior_value, elapsed_seconds):
            reason = "jump_rejected"
        elif corroboration is not None:
            # M5.8 camera path: several agreeing, clean frames -- never one.
            # Runs INSTEAD of the single-tick confidence tier below, not in
            # addition to it, and only ever after range/jump have passed.
            verdict = live_corroboration.evaluate(evidence, raw_value)
            accept = verdict.accepted
            run_length = verdict.agreeing_samples
            confidence = verdict.mean_confidence
            reason = {
                "corroborated": "corroborated",
                "corroborated_recovery": "corroborated_recovery",
                "geometry": "crop_geometry",
                "low_confidence": "low_confidence",
            }.get(verdict.reason, "awaiting_corroboration")
            if verdict.reason == "corroborated":
                # A fully corroborated, high-confidence read is the clean
                # case -- nothing for a human to review, so it raises no
                # flagged entry (same posture as the pre-M5.8 ai_high tier).
                reason = None
            elif verdict.reason == "corroborated_recovery" and raw_value == prior_value:
                # The recovery tier accepted a value the operator has
                # already been shown a review item for -- this tick merely
                # re-confirms the SAME number the camera keeps reading. A
                # steady 88 bpm at 65% mean confidence would otherwise raise
                # one FlaggedReading per tick for the whole case (measured:
                # 36 rows across 14 frames on the real monitor frames), which
                # is the same review-queue flooding _ROUTINE_HOLD_REASONS
                # exists to stop, just arriving through the accept branch.
                # A recovery-tier acceptance of a CHANGED value still flags,
                # exactly once, when it changes -- the same "one entry per
                # genuine change" rule the ledger itself follows.
                reason = None
        else:
            tier = confidence_tier(confidence)
            if tier == "ai_low":
                reason = "low_confidence"
                # M5.4: the ONLY new acceptance path. Never reached unless
                # range, jump AND the existing confidence gate have already
                # been evaluated above -- this can only add a path to
                # confirmation for a reading the gate already refused, never
                # override a rejection or a gate the reading already clears.
                if temporal_state is not None and temporal_signal.is_corroborated(field_state, confidence):
                    accept = True
                    reason = "temporal_corroboration"
            elif tier == "ai_medium":
                accept = True
                reason = "medium_confidence"
            else:
                accept = True

        if accept:
            final_value = raw_value
            new_confirmed[field] = FieldState(value=raw_value, timestamp=now_ms)
            status = "confirmed"
        else:
            if prior_value is not None:
                final_value = prior_value
                status = "held"
            elif not allow_baseline:
                # M5.8: nothing has ever been confirmed for this field and
                # this tick did not confirm it either. Show NOTHING rather
                # than inventing a plausible number -- and write nothing into
                # new_confirmed, so the next tick reaches this same branch
                # instead of "holding" a value no camera ever read. See
                # allow_baseline's parameter docstring.
                final_value = None
                reason = "unconfirmed" if reason is None else reason
                status = "unknown"
            else:
                # No history at all (shouldn't happen once initial_confirmed_state
                # has seeded a baseline, but stay safe rather than emit None).
                # This is more important to surface than the original
                # rejection reason, so it always wins here. Record it into
                # new_confirmed too, so the NEXT tick has real continuity to
                # hold/compare against instead of repeating this fallback
                # indefinitely — still flagged as a baseline this tick.
                final_value = raw_value if raw_value is not None else DEFAULT_BASELINE[field]
                reason = "baseline_unconfirmed"
                new_confirmed[field] = FieldState(value=final_value, timestamp=now_ms)
                status = "baseline"

        if field_status is not None:
            field_status[field] = status

        reading[field] = final_value
        field_reason[field] = reason
        field_confidence[field] = confidence
        field_run_length[field] = run_length
        raw_values[field] = raw_value
        final_values[field] = final_value

    flagged: List[dict] = []
    for vital, group_fields in VITAL_GROUPS.items():
        reasons = [field_reason[f] for f in group_fields if field_reason[f] is not None]
        if not reasons:
            continue
        # M5.8, camera path only: don't raise a review item for the pipeline
        # simply not having confirmed anything yet -- see
        # _ROUTINE_HOLD_REASONS. A group with even one non-routine reason
        # (rejected range, implausible jump, suspicious crop geometry,
        # accepted-via-recovery) is still flagged in full, notes included.
        if corroboration is not None and all(r in _ROUTINE_HOLD_REASONS for r in reasons):
            continue

        severity = "critical" if any(r in _CRITICAL_REASONS for r in reasons) else "warning"
        confidence = field_confidence[group_fields[0]]
        primary_field = group_fields[0]
        primary_unit = UNITS[primary_field] if len(group_fields) == 1 else UNITS[group_fields[0]]

        notes = [
            _reason_note(
                f, field_reason[f], raw_values[f],
                last_confirmed.get(f).value if last_confirmed.get(f) else None,
                field_confidence[f], field_run_length[f],
            )
            for f in group_fields
            if field_reason[f] is not None
        ]

        flagged.append(
            {
                "timestamp": now_ms,
                "vital": vital,
                "aiValue": _format_group_value(group_fields, raw_values),
                "suggestedValue": _format_group_value(group_fields, final_values),
                "unit": primary_unit,
                "confidence": confidence,
                "severity": severity,
                "status": "pending",
                "correctedValue": None,
                "frameNote": "; ".join(notes),
            }
        )

    return reading, new_confirmed, flagged
