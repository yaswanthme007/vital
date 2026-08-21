from typing import Dict, Optional, Tuple

# Physiological hard bounds — a value outside these is IMPLAUSIBLE regardless
# of OCR confidence (this is what catches S5's "confident 6.8°C" case: 6.8 is
# outside temp's [30, 44] bounds even at high OCR confidence). nibpMean isn't
# given an explicit bound by the task; (20, 220) is a superset consistent
# with the systolic/diastolic bounds' spread.
#
# hr's lower bound is 0, not 20 (M4.4, root-caused in TIER2_M4_3_RELIABILITY
# _REPORT.md §8): a real monitor can genuinely display HR=0 during asystole/
# a lead-off or code-blue alarm state -- M4.1's manually-transcribed ground
# truth confirms this dataset's monitor does exactly that, and a monitor
# that can display 0 is not displaying an OCR artifact when OCR reads 0.
# RANGE_BOUNDS is a VALIDITY check ("is this a physically representable
# reading"), not a CLINICAL-SEVERITY check ("is this reading dangerous") --
# that second question is check_alerts()'s job (app/alerts/rules.py), which
# already fires a CRITICAL "Heart Rate CRITICALLY LOW" alert for any
# confirmed hr<=40, HR=0 included, with zero changes needed there. Excluding
# 0 from validity conflated the two concerns: it didn't just fail to flag a
# dangerous reading as dangerous, it discarded the reading entirely, so nothing
# was ever displayed AND no alert could ever fire either (see reconcile()'s
# hold-last-confirmed behavior). Only truly non-physical values (negative,
# or absurdly high) are still rejected as implausible.
RANGE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "hr": (0, 250),
    "spo2": (50, 100),
    "temp": (30, 44),
    "etco2": (0, 100),
    "rr": (0, 60),
    "nibpSystolic": (40, 260),
    "nibpDiastolic": (20, 180),
    "nibpMean": (20, 220),
}

# temp's RANGE_BOUNDS above is Celsius, and that assumption runs deep --
# DEFAULT_BASELINE's 36.8, check_alerts()'s 35.5/38.5/39.5 thresholds, and
# the frontend's "°C" unit label (reconcile.py UNITS) are all Celsius too.
# Some monitors (M4.1's external-video dataset included) display Fahrenheit
# instead -- M4.3 found this reduces Temp's confirmed accuracy to 0% even
# though OCR reads it correctly on every single frame (every read gets
# rejected as implausible_range). There is currently no per-monitor/session
# unit setting anywhere in the data model (CalibrationProfile, Session,
# VitalReading all lack one) and reconcile() has no session/calibration
# context available to it at all -- adding that plumbing would mean
# threading a new setting through calibration -> source -> read_frame ->
# reconcile, a materially bigger architecture change than this milestone is
# scoped to make. Human body temperature doesn't need that: it is uniquely
# self-describing by magnitude alone. A real body temperature is either in
# RANGE_BOUNDS["temp"] (Celsius) or in the Fahrenheit-equivalent band below
# (the same bounds converted) -- and those two bands never overlap (44 < 86),
# so there is no value a reading could take that's ambiguous between the two
# unit systems. FAHRENHEIT_BOUNDS is therefore derived, not independently
# chosen, so the two can never drift out of sync.
FAHRENHEIT_BOUNDS: Tuple[float, float] = tuple(c * 9.0 / 5.0 + 32.0 for c in RANGE_BOUNDS["temp"])  # (86.0, 111.2)

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


def normalize_temp_celsius(raw_value: float) -> float:
    """Converts a Fahrenheit-scale raw temp OCR reading to Celsius; leaves
    anything else unchanged (including values RANGE_BOUNDS will go on to
    reject as implausible — this function's job is only unit normalization,
    not validation). See RANGE_BOUNDS/FAHRENHEIT_BOUNDS above for why the
    magnitude-only detection is safe. Called from reconcile() before any
    validation runs, so a Fahrenheit reading is normalized once and then
    flows through range/jump/confidence checks, storage, and display
    exactly like any other Celsius reading -- nothing downstream needs to
    know a conversion happened."""
    lo_c, hi_c = RANGE_BOUNDS["temp"]
    if lo_c <= raw_value <= hi_c:
        return raw_value
    lo_f, hi_f = FAHRENHEIT_BOUNDS
    if lo_f <= raw_value <= hi_f:
        return (raw_value - 32.0) * 5.0 / 9.0
    return raw_value


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
