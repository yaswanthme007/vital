import pytest

from app.validation.rules import (
    FIELDS,
    JUMP_LIMITS,
    RANGE_BOUNDS,
    confidence_tier,
    is_in_range,
    is_jump_rejected,
    normalize_temp_celsius,
)


def test_every_field_has_range_and_jump_bounds():
    assert set(RANGE_BOUNDS.keys()) == set(FIELDS)
    assert set(JUMP_LIMITS.keys()) == set(FIELDS)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("hr", 72, True),
        ("hr", 251, False),
        ("hr", 250, True),  # inclusive upper bound
        # M4.4: hr's lower bound widened 20->0 -- a monitor can genuinely
        # display 0 (asystole/alarm state), see rules.py's RANGE_BOUNDS
        # comment and TIER2_M4_4_RULES_LAYER_REPORT.md §2/§5. Validity and
        # clinical severity are separate concerns now: check_alerts() still
        # fires a CRITICAL alert for a confirmed hr<=40, 0 included.
        ("hr", 0, True),  # inclusive lower bound
        ("hr", -1, False),  # negative hr is not physically representable
        ("hr", 1, True),
        ("hr", 19, True),  # was rejected pre-M4.4; now validly observable
        ("spo2", 98, True),
        ("spo2", 49, False),
        ("spo2", 101, False),
        ("temp", 36.8, True),
        ("temp", 6.8, False),  # the S5 "confident 6.8°C" case
        ("temp", 29.9, False),
        ("temp", 44.1, False),
        ("etco2", 38, True),
        ("etco2", -1, False),
        ("etco2", 101, False),
        ("rr", 14, True),
        ("rr", 61, False),
        ("nibpSystolic", 120, True),
        ("nibpSystolic", 39, False),
        ("nibpSystolic", 261, False),
        ("nibpDiastolic", 78, True),
        ("nibpDiastolic", 19, False),
        ("nibpDiastolic", 181, False),
        ("nibpMean", 92, True),
        ("nibpMean", 19, False),
        ("nibpMean", 221, False),
    ],
)
def test_is_in_range(field, value, expected):
    assert is_in_range(field, value) is expected


# ─── M4.4: temperature unit normalization (36.8°C and 98.6°F both valid) ────


@pytest.mark.parametrize(
    "raw_value,expected_celsius",
    [
        (36.8, 36.8),  # already Celsius -- unchanged
        (30.0, 30.0),  # Celsius lower bound -- unchanged
        (44.0, 44.0),  # Celsius upper bound -- unchanged
        (98.6, 37.0),  # Fahrenheit -- converted
        (86.0, 30.0),  # Fahrenheit band lower edge -- converts to Celsius lower bound
        (111.2, 44.0),  # Fahrenheit band upper edge -- converts to Celsius upper bound
        (100.4, 38.0),  # Fahrenheit fever (100.4F) -- converted
        (60.0, 60.0),  # neither band (the 44-86 gap) -- left unchanged, still garbage
        (6.8, 6.8),  # the S5 "confident 6.8°C" case -- left unchanged, is_in_range rejects it
        (200.0, 200.0),  # neither band -- left unchanged
    ],
)
def test_normalize_temp_celsius(raw_value, expected_celsius):
    assert normalize_temp_celsius(raw_value) == pytest.approx(expected_celsius, abs=1e-9)


def test_normalize_temp_celsius_then_is_in_range_end_to_end():
    """The two functions compose the way reconcile() actually calls them:
    normalize first, then validate. A Fahrenheit body temp passes; a value
    in neither unit's plausible band still correctly fails."""
    assert is_in_range("temp", normalize_temp_celsius(98.6)) is True
    assert is_in_range("temp", normalize_temp_celsius(36.8)) is True
    assert is_in_range("temp", normalize_temp_celsius(60.0)) is False
    assert is_in_range("temp", normalize_temp_celsius(6.8)) is False


def test_hr_jump_rejected_matches_task_example():
    # "hr jumps >40 in <3s" -> 72 -> 172 within 1s must be rejected.
    assert is_jump_rejected("hr", 172, 72, elapsed_seconds=1.0) is True


def test_hr_jump_within_limit_is_not_rejected():
    assert is_jump_rejected("hr", 100, 72, elapsed_seconds=1.0) is False  # delta 28 < 40


def test_jump_not_rejected_outside_the_time_window():
    # Same big delta, but enough time has passed that it's plausible.
    assert is_jump_rejected("hr", 172, 72, elapsed_seconds=30.0) is False


def test_jump_not_rejected_without_prior_value_or_elapsed():
    assert is_jump_rejected("hr", 172, None, elapsed_seconds=1.0) is False
    assert is_jump_rejected("hr", 172, 72, elapsed_seconds=None) is False


def test_jump_boundary_is_exclusive():
    # exactly max_delta apart -> not rejected ("more than" in the task wording)
    assert is_jump_rejected("hr", 112, 72, elapsed_seconds=1.0) is False  # delta == 40
    assert is_jump_rejected("hr", 112.01, 72, elapsed_seconds=1.0) is True


@pytest.mark.parametrize(
    "confidence,expected_tier",
    [
        (100, "ai_high"),
        (90, "ai_high"),
        (89.9, "ai_medium"),
        (70, "ai_medium"),
        (69.9, "ai_low"),
        (0, "ai_low"),
    ],
)
def test_confidence_tier_boundaries(confidence, expected_tier):
    assert confidence_tier(confidence) == expected_tier
