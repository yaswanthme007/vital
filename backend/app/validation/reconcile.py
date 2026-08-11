from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.validation.rules import (
    CONFIDENCE_MEDIUM_MIN,
    FIELD_TO_VITAL_GROUP,
    FIELDS,
    JUMP_LIMITS,
    RANGE_BOUNDS,
    confidence_tier,
    is_in_range,
    is_jump_rejected,
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

_REASON_TEXT = {
    "unreadable": "OCR could not read a value this tick",
    "implausible_range": "{value} is outside the physiologically plausible range {bounds}",
    "jump_rejected": "{value} is an implausible jump from the last confirmed {last_value} within {window:g}s",
    "low_confidence": "OCR confidence {confidence:.0f}% is below the ai_low threshold ({tier_min:.0f}%)",
    "medium_confidence": "OCR confidence {confidence:.0f}% is ai_medium ({tier_min:.0f}-89%) — accepted but flagged for review",
    "baseline_unconfirmed": "no confirmed reading yet — holding the pre-session baseline value",
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


def _reason_note(field: str, reason: str, value: Optional[float], last_value: Optional[float], confidence: float) -> str:
    bounds = RANGE_BOUNDS.get(field)
    window = JUMP_LIMITS_WINDOW.get(field)
    text = _REASON_TEXT[reason].format(
        value=_fmt_number(value),
        last_value=_fmt_number(last_value),
        bounds=bounds,
        window=window or 0.0,
        confidence=confidence,
        tier_min=CONFIDENCE_MEDIUM_MIN,
    )
    return f"{field}: {text}"


def reconcile(
    raw_reading: dict,
    per_vital_confidence: Optional[Dict[str, float]],
    last_confirmed: Dict[str, FieldState],
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
    """
    per_vital_confidence = per_vital_confidence or {}
    now_ms = raw_reading.get("timestamp")

    reading: dict = {"timestamp": raw_reading.get("timestamp")}
    new_confirmed: Dict[str, FieldState] = dict(last_confirmed)
    # Per-field bookkeeping used to build grouped (NIBP) flags after the
    # per-field accept/hold decisions are all made.
    field_reason: Dict[str, Optional[str]] = {}
    field_confidence: Dict[str, float] = {}
    raw_values: Dict[str, Optional[float]] = {}
    final_values: Dict[str, Optional[float]] = {}

    for field in FIELDS:
        vital_group = FIELD_TO_VITAL_GROUP[field]
        confidence = per_vital_confidence.get(vital_group, 0.0)
        raw_value = raw_reading.get(field)
        prior = last_confirmed.get(field)
        prior_value = prior.value if prior else None
        prior_ts = prior.timestamp if prior else None
        elapsed_seconds = ((now_ms - prior_ts) / 1000.0) if (prior_ts is not None and now_ms is not None) else None

        reason: Optional[str] = None
        accept = False

        if raw_value is None:
            reason = "unreadable"
        elif not is_in_range(field, raw_value):
            reason = "implausible_range"
        elif is_jump_rejected(field, raw_value, prior_value, elapsed_seconds):
            reason = "jump_rejected"
        else:
            tier = confidence_tier(confidence)
            if tier == "ai_low":
                reason = "low_confidence"
            elif tier == "ai_medium":
                accept = True
                reason = "medium_confidence"
            else:
                accept = True

        if accept:
            final_value = raw_value
            new_confirmed[field] = FieldState(value=raw_value, timestamp=now_ms)
        else:
            if prior_value is not None:
                final_value = prior_value
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

        reading[field] = final_value
        field_reason[field] = reason
        field_confidence[field] = confidence
        raw_values[field] = raw_value
        final_values[field] = final_value

    flagged: List[dict] = []
    for vital, group_fields in VITAL_GROUPS.items():
        reasons = [field_reason[f] for f in group_fields if field_reason[f] is not None]
        if not reasons:
            continue

        severity = "critical" if any(r in _CRITICAL_REASONS for r in reasons) else "warning"
        confidence = field_confidence[group_fields[0]]
        primary_field = group_fields[0]
        primary_unit = UNITS[primary_field] if len(group_fields) == 1 else UNITS[group_fields[0]]

        notes = [
            _reason_note(f, field_reason[f], raw_values[f], last_confirmed.get(f).value if last_confirmed.get(f) else None, field_confidence[f])
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
