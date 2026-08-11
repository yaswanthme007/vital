from typing import Dict, Optional, Tuple

# Physiological hard bounds — a value outside these is IMPLAUSIBLE regardless
# of OCR confidence (this is what catches S5's "confident 6.8°C" case: 6.8 is
# outside temp's [30, 44] bounds even at high OCR confidence). nibpMean isn't
# given an explicit bound by the task; (20, 220) is a superset consistent
# with the systolic/diastolic bounds' spread.
RANGE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "hr": (20, 250),
    "spo2": (50, 100),
    "temp": (30, 44),
    "etco2": (0, 100),
    "rr": (0, 60),
    "nibpSystolic": (40, 260),
    "nibpDiastolic": (20, 180),
    "nibpMean": (20, 220),
}

# (max_delta, window_seconds): a change bigger than max_delta within window
# vs the last CONFIRMED value looks like a misread, not a real physiological
# swing. hr's (40, 3.0) is the task's own example; the rest are
# physiologically-reasonable analogues (see plan for reasoning) chosen to be
# generous relative to synthetic mode's per-tick step sizes so normal drift
# never trips them.
JUMP_LIMITS: Dict[str, Tuple[float, float]] = {
    "hr": (40, 3.0),
    "spo2": (15, 3.0),
    "temp": (2.0, 3.0),
    "etco2": (20, 3.0),
    "rr": (15, 3.0),
    "nibpSystolic": (40, 3.0),
    "nibpDiastolic": (30, 3.0),
    "nibpMean": (35, 3.0),
}

# Maps the 8 numeric VitalReading fields down to the 6 vital "groups" used
# for OCR confidence lookups and FlaggedReading.vital (NIBP's 3 fields share
# one OCR crop, hence one confidence value).
FIELD_TO_VITAL_GROUP: Dict[str, str] = {
    "hr": "hr",
    "spo2": "spo2",
    "etco2": "etco2",
    "temp": "temp",
    "rr": "rr",
    "nibpSystolic": "nibp",
    "nibpDiastolic": "nibp",
    "nibpMean": "nibp",
}

FIELDS: Tuple[str, ...] = tuple(RANGE_BOUNDS.keys())

# Confidence tiers (S8's own — distinct from app/sources/replay.py's rough
# 80/50 frame-level provenance heuristic computed upstream; these 90/70
# thresholds are the authoritative per-field accept/hold decision).
CONFIDENCE_HIGH_MIN = 90
CONFIDENCE_MEDIUM_MIN = 70


def is_in_range(field: str, value: float) -> bool:
    bounds = RANGE_BOUNDS.get(field)
    if bounds is None:
        return True
    lo, hi = bounds
    return lo <= value <= hi


def is_jump_rejected(
    field: str,
    value: float,
    last_value: Optional[float],
    elapsed_seconds: Optional[float],
) -> bool:
    """True if `value` looks like a misread jump vs the last CONFIRMED value
    (large delta within a short window). No prior value/elapsed to compare
    against ⇒ never jump-rejected (nothing to jump from)."""
    if last_value is None or elapsed_seconds is None:
        return False
    max_delta, window = JUMP_LIMITS.get(field, (float("inf"), 0.0))
    if elapsed_seconds >= window:
        return False
    return abs(value - last_value) > max_delta


def confidence_tier(confidence: float) -> str:
    """>=90 ai_high (accept), 70-89 ai_medium (accept but flag), <70 ai_low
    (do not accept as current value)."""
    if confidence >= CONFIDENCE_HIGH_MIN:
        return "ai_high"
    if confidence >= CONFIDENCE_MEDIUM_MIN:
        return "ai_medium"
    return "ai_low"
