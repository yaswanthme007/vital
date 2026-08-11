import pytest

from app.validation.rules import (
    FIELDS,
    JUMP_LIMITS,
    RANGE_BOUNDS,
    confidence_tier,
    is_in_range,
    is_jump_rejected,
)


def test_every_field_has_range_and_jump_bounds():
    assert set(RANGE_BOUNDS.keys()) == set(FIELDS)
    assert set(JUMP_LIMITS.keys()) == set(FIELDS)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("hr", 72, True),
        ("hr", 19, False),
        ("hr", 251, False),
        ("hr", 20, True),  # inclusive lower bound
        ("hr", 250, True),  # inclusive upper bound
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
