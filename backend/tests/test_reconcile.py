from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile

T = 1_700_000_000_000  # base timestamp, ms


def _confirmed(overrides=None, at=T - 10_000):
    """A last_confirmed state where every field was confirmed 10s ago (well
    outside every jump window) unless overridden."""
    state = initial_confirmed_state(at)
    if overrides:
        for field, value in overrides.items():
            state[field] = FieldState(value=value, timestamp=at)
    return state


def _reading(**overrides):
    base = {
        "hr": 74,
        "spo2": 98,
        "nibpSystolic": 120,
        "nibpDiastolic": 78,
        "nibpMean": 92,
        "etco2": 38,
        "temp": 36.8,
        "rr": 14,
        "timestamp": T,
    }
    base.update(overrides)
    return base


def _confidence(**overrides):
    base = {v: 95.0 for v in ("hr", "spo2", "nibp", "etco2", "temp", "rr")}
    base.update(overrides)
    return base


def _flag_for(flagged, vital):
    matches = [f for f in flagged if f["vital"] == vital]
    assert len(matches) == 1, f"expected exactly one flagged entry for {vital}, got {matches}"
    return matches[0]


# ─── Golden set ───────────────────────────────────────────────────────────
# Each case: (name, raw_reading, per_vital_confidence, last_confirmed) ->
# assertions on the reconciled reading + flagged output.


def test_golden_confident_range_miss_is_range_rejected():
    """The exact S5 case: OCR confidently reads temp=6.8 (should be 36.8).
    High confidence must NOT save it — range is checked first."""
    raw = _reading(temp=6.8)
    confidence = _confidence(temp=99.0)  # deliberately high — confidence must not override range
    last_confirmed = _confirmed({"temp": 36.8})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["temp"] == 36.8, "must hold the last confirmed value, not the implausible 6.8"
    flag = _flag_for(flagged, "temp")
    assert flag["severity"] == "critical"
    assert flag["aiValue"] == "6.8"
    assert flag["suggestedValue"] == "36.8"
    assert updated["temp"].value == 36.8, "a rejected field must not advance last_confirmed"


def test_golden_hr_jump_is_jump_rejected():
    """72 -> 172 within the 3s window must be rejected even at high confidence."""
    raw = _reading(hr=172)
    confidence = _confidence(hr=99.0)
    last_confirmed = _confirmed({"hr": 72}, at=T - 1_000)  # confirmed 1s ago

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 72
    flag = _flag_for(flagged, "hr")
    assert flag["severity"] == "critical"
    assert flag["aiValue"] == "172"
    assert flag["suggestedValue"] == "72"
    assert updated["hr"].value == 72


def test_golden_hr_jump_allowed_after_the_window_passes():
    """Same big delta, but enough time has passed that it's plausible — must
    NOT be rejected (proves this isn't just an absolute-delta cap)."""
    raw = _reading(hr=172)
    confidence = _confidence(hr=99.0)
    last_confirmed = _confirmed({"hr": 72}, at=T - 30_000)  # confirmed 30s ago

    reading, _updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 172
    assert not any(f["vital"] == "hr" for f in flagged)


def test_golden_ai_low_confidence_is_held():
    raw = _reading(spo2=95)
    confidence = _confidence(spo2=65.0)  # < 70 -> ai_low
    last_confirmed = _confirmed({"spo2": 98})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["spo2"] == 98, "ai_low must not become the current value"
    flag = _flag_for(flagged, "spo2")
    assert flag["severity"] == "warning"
    assert flag["aiValue"] == "95"
    assert flag["suggestedValue"] == "98"
    assert updated["spo2"].value == 98, "held values must not advance last_confirmed"


def test_golden_ai_medium_confidence_is_accepted_and_flagged():
    raw = _reading(rr=16)
    confidence = _confidence(rr=80.0)  # 70-89 -> ai_medium
    last_confirmed = _confirmed({"rr": 14})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["rr"] == 16, "ai_medium IS accepted as the current value"
    flag = _flag_for(flagged, "rr")
    assert flag["severity"] == "warning"
    assert flag["aiValue"] == "16"
    assert flag["suggestedValue"] == "16"
    assert updated["rr"].value == 16, "accepted values DO advance last_confirmed"


def test_golden_ai_high_confidence_accepted_with_no_flag():
    raw = _reading(hr=76)
    confidence = _confidence(hr=95.0)
    last_confirmed = _confirmed({"hr": 74})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 76
    assert not any(f["vital"] == "hr" for f in flagged)
    assert updated["hr"].value == 76


def test_golden_unreadable_field_is_held():
    raw = _reading(hr=None)
    confidence = _confidence(hr=0.0)
    last_confirmed = _confirmed({"hr": 74})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 74
    flag = _flag_for(flagged, "hr")
    assert flag["severity"] == "warning"
    assert flag["aiValue"] == "?"
    assert flag["suggestedValue"] == "74"
    assert updated["hr"].value == 74


def test_golden_nibp_grouped_flag_uses_sys_dia_string():
    """NIBP's 3 fields are validated independently but collapse into ONE
    flagged entry formatted as 'sys/dia' — matching the frontend's own mock
    data shape, not three separate flags."""
    raw = _reading(nibpSystolic=500, nibpDiastolic=80, nibpMean=93)  # 500 implausible; 80/93 fine
    confidence = _confidence(nibp=95.0)
    last_confirmed = _confirmed({"nibpSystolic": 120, "nibpDiastolic": 78, "nibpMean": 92})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["nibpSystolic"] == 120, "systolic held (implausible)"
    assert reading["nibpDiastolic"] == 80, "diastolic accepted (genuinely new value)"
    assert reading["nibpMean"] == 93, "mean accepted independently"

    nibp_flags = [f for f in flagged if f["vital"] == "nibp"]
    assert len(nibp_flags) == 1, "NIBP must collapse to exactly one flagged entry, not three"
    flag = nibp_flags[0]
    assert flag["severity"] == "critical"
    assert flag["aiValue"] == "500/80"
    assert flag["suggestedValue"] == "120/80"

    assert updated["nibpSystolic"].value == 120
    assert updated["nibpDiastolic"].value == 80
    assert updated["nibpMean"].value == 93


# ─── Hold-last-confirmed always-complete guarantee ──────────────────────────


def test_reading_is_always_complete_even_when_every_field_is_rejected():
    raw = _reading(hr=999, spo2=1, temp=200, etco2=-5, rr=999, nibpSystolic=1, nibpDiastolic=1, nibpMean=1)
    confidence = _confidence()
    last_confirmed = _confirmed()

    reading, _updated, flagged = reconcile(raw, confidence, last_confirmed)

    for field in ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr"):
        assert reading[field] is not None, f"{field} must never be null in the emitted reading"
    assert len(flagged) == 6  # every vital group flagged (nibp collapses to 1)


def test_bootstrap_baseline_used_when_no_history_at_all():
    """The true first-tick edge case: no last_confirmed entry exists for a
    field at all (not even a seeded baseline). Must fall back to
    DEFAULT_BASELINE rather than emit null, and say so honestly."""
    raw = _reading(hr=None)
    confidence = _confidence(hr=0.0)

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed={})

    assert reading["hr"] == 75  # DEFAULT_BASELINE["hr"]
    flag = _flag_for(flagged, "hr")
    assert "baseline" in flag["frameNote"].lower()
    assert updated["hr"].value == 75


def test_initial_confirmed_state_seeds_every_field():
    state = initial_confirmed_state(T)
    for field in ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr"):
        assert field in state
        assert state[field].timestamp == T


# ─── field_status out-param (M5.7) ──────────────────────────────────────────
#
# Surfaces reconcile()'s internal accept/hold/baseline decision without
# changing the 3-value return contract — see reconcile()'s own docstring for
# why an in-place OUT-param was chosen (same pattern as temporal_state /
# crop_integrity). This is what app.ws.vitals.send_loop uses to decide what
# is a genuine OBSERVATION (persist-worthy) vs merely a held display value.


def test_field_status_defaults_to_none_and_is_byte_identical_to_pre_m5_7():
    """Every pre-M5.7 call site omits field_status. Confirm doing so is
    completely inert -- same 3 return values, nothing extra mutated."""
    raw = _reading(hr=76)
    confidence = _confidence(hr=95.0)
    last_confirmed = _confirmed({"hr": 74})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 76
    assert updated["hr"].value == 76
    assert isinstance(flagged, list)


def test_field_status_reports_confirmed_for_an_accepted_field():
    raw = _reading(hr=76)
    confidence = _confidence(hr=95.0)
    last_confirmed = _confirmed({"hr": 74})
    field_status = {}

    reconcile(raw, confidence, last_confirmed, field_status=field_status)

    assert field_status["hr"] == "confirmed"


def test_field_status_reports_held_for_a_rejected_field_with_prior_history():
    """Covers range-rejected, jump-rejected, ai_low and unreadable -- every
    one of them holds the prior value, and every one must report 'held', not
    just the ones already covered above."""
    raw = _reading(temp=6.8, hr=172, spo2=95, rr=None)
    confidence = _confidence(temp=99.0, hr=99.0, spo2=65.0, rr=0.0)
    last_confirmed = _confirmed({"temp": 36.8, "hr": 72, "spo2": 98, "rr": 14}, at=T - 1_000)
    field_status = {}

    reading, _updated, _flagged = reconcile(raw, confidence, last_confirmed, field_status=field_status)

    assert reading["temp"] == 36.8 and field_status["temp"] == "held"
    assert reading["hr"] == 72 and field_status["hr"] == "held"
    assert reading["spo2"] == 98 and field_status["spo2"] == "held"
    assert reading["rr"] == 14 and field_status["rr"] == "held"


def test_field_status_reports_baseline_only_when_there_is_no_prior_history():
    raw = _reading(hr=None)
    confidence = _confidence(hr=0.0)
    field_status = {}

    reading, _updated, flagged = reconcile(raw, confidence, last_confirmed={}, field_status=field_status)

    assert reading["hr"] == 75  # DEFAULT_BASELINE["hr"]
    assert field_status["hr"] == "baseline"
    assert "baseline" in _flag_for(flagged, "hr")["frameNote"].lower()


def test_field_status_ai_medium_is_confirmed_not_just_accepted():
    raw = _reading(rr=16)
    confidence = _confidence(rr=80.0)  # ai_medium -- accepted AND flagged
    last_confirmed = _confirmed({"rr": 14})
    field_status = {}

    reconcile(raw, confidence, last_confirmed, field_status=field_status)

    assert field_status["rr"] == "confirmed", "ai_medium is a genuine observation, not a held one"


def test_field_status_covers_every_field_every_tick():
    raw = _reading()
    confidence = _confidence()
    last_confirmed = _confirmed()
    field_status = {}

    reconcile(raw, confidence, last_confirmed, field_status=field_status)

    for field in ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr"):
        assert field in field_status
        assert field_status[field] in ("confirmed", "held", "baseline")
