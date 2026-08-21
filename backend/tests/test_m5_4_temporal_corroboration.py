"""M5.4: temporal corroboration. Focused regression tests for the new
app.validation.temporal module and the one new branch it adds inside
app.validation.reconcile.reconcile() — see
docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md for the evidence this design is
built from.

Every test here either (a) proves the new path is COMPLETELY INERT unless a
caller explicitly opts in by passing a temporal_state dict (backward
compatibility, byte-for-byte), or (b) proves the new path, once opted into,
still cannot bypass range/jump checks, still cannot be triggered by a
tracking/extraction failure, and still requires real evidence (a confidence
floor AND several repeated observations) before it fires.
"""

import asyncio
from collections import deque

from app.alerts.rules import AlertThrottle
from app.sources.base import Frame, VitalsSource
from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile
from app.validation.temporal import (
    CONFIDENCE_TEMPORAL_FLOOR,
    TEMPORAL_AGREEMENT_MIN_RUN,
    TemporalFieldState,
    initial_temporal_state,
    is_corroborated,
    observe,
)

T = 1_700_000_000_000  # base timestamp, ms
TICK_MS = 1_000


def _confirmed(overrides=None, at=T - 10_000):
    state = initial_confirmed_state(at)
    if overrides:
        for field, value in overrides.items():
            state[field] = FieldState(value=value, timestamp=at)
    return state


def _reading(**overrides):
    base = {
        "hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78,
        "nibpMean": 92, "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": T,
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


def _run_ticks(n, hr_value, hr_confidence, temporal_state, start_ts=T):
    """Feeds `n` identical HR ticks through reconcile() in order, all other
    fields held at a stable high-confidence baseline so only HR is under
    test. Returns the LAST tick's (reading, updated, flagged)."""
    confirmed = _confirmed(at=start_ts - 10_000)
    result = None
    for i in range(n):
        raw = _reading(hr=hr_value, timestamp=start_ts + i * TICK_MS)
        conf = _confidence(hr=hr_confidence)
        result = reconcile(raw, conf, confirmed, temporal_state=temporal_state)
        _, confirmed, _ = result
    return result


# ═══════════════════════════════════════════════════════════════════════
# app.validation.temporal unit tests
# ═══════════════════════════════════════════════════════════════════════


def test_observe_resets_on_missing_read():
    state = TemporalFieldState(last_value=85.0, run_length=4)
    reset = observe(state, None)
    assert reset.run_length == 0
    assert reset.last_value is None


def test_observe_resets_on_value_change():
    state = TemporalFieldState(last_value=85.0, run_length=4)
    changed = observe(state, 86.0)
    assert changed.run_length == 1
    assert changed.last_value == 86.0


def test_observe_increments_on_repeated_value():
    state = TemporalFieldState()
    for expected_len, value in enumerate([85.0, 85.0, 85.0], start=1):
        state = observe(state, value)
        assert state.run_length == expected_len
        assert state.last_value == 85.0


def test_is_corroborated_requires_both_floor_and_run_length():
    strong_run = TemporalFieldState(last_value=85.0, run_length=TEMPORAL_AGREEMENT_MIN_RUN)
    weak_run = TemporalFieldState(last_value=85.0, run_length=TEMPORAL_AGREEMENT_MIN_RUN - 1)

    assert is_corroborated(strong_run, CONFIDENCE_TEMPORAL_FLOOR) is True
    assert is_corroborated(strong_run, CONFIDENCE_TEMPORAL_FLOOR - 1) is False, \
        "a long run at near-zero confidence must still not corroborate"
    assert is_corroborated(weak_run, 99.0) is False, \
        "a single (or short) run must not corroborate regardless of confidence"


def test_initial_temporal_state_is_fresh_and_covers_every_field():
    from app.validation.rules import FIELDS

    state = initial_temporal_state()
    assert set(state.keys()) == set(FIELDS)
    assert all(s.run_length == 0 and s.last_value is None for s in state.values())


# ═══════════════════════════════════════════════════════════════════════
# reconcile() — backward compatibility (temporal_state=None, the default)
# ═══════════════════════════════════════════════════════════════════════


def test_reconcile_without_temporal_state_is_unaffected_by_repetition():
    """The exact pre-M5.4 contract: repeating the SAME low-confidence value
    forever must never confirm it unless the caller explicitly opts in."""
    confirmed = _confirmed({"hr": 74})
    for i in range(10):
        raw = _reading(hr=85, timestamp=T + i * TICK_MS)
        conf = _confidence(hr=55.0)  # clears the temporal floor, never the real gate
        reading, confirmed, flagged = reconcile(raw, conf, confirmed)  # no temporal_state
        assert reading["hr"] == 74, "must stay held — temporal_state was never provided"
        flag = _flag_for(flagged, "hr")
        assert flag["frameNote"].startswith("hr: OCR confidence")
        assert "temporal" not in flag["frameNote"]


def test_high_confidence_correct_still_confirms_with_temporal_state_present():
    """Item 1: a normal ai_high read is unaffected by merely PASSING a
    temporal_state — the new branch only ever engages inside the ai_low
    tier."""
    confirmed = _confirmed({"hr": 74})
    temporal_state = initial_temporal_state()
    raw = _reading(hr=85)
    conf = _confidence(hr=95.0)
    reading, _updated, flagged = reconcile(raw, conf, confirmed, temporal_state=temporal_state)
    assert reading["hr"] == 85
    assert not flagged  # ai_high: accepted cleanly, never flagged


def test_high_confidence_wrong_value_is_not_shielded_by_temporal_state():
    """Item 2: temporal corroboration must never be consulted for a value
    range/jump already rejects — 'wrong' here means physiologically
    implausible, the one kind of wrongness reconcile() can detect on its
    own without ground truth."""
    confirmed = _confirmed({"temp": 36.8})
    temporal_state = initial_temporal_state()
    raw = _reading(temp=6.8)
    conf = _confidence(temp=99.0)
    reading, _updated, flagged = reconcile(raw, conf, confirmed, temporal_state=temporal_state)
    assert reading["temp"] == 36.8
    flag = _flag_for(flagged, "temp")
    assert flag["severity"] == "critical"


# ═══════════════════════════════════════════════════════════════════════
# reconcile() — the new temporal_corroboration path, opted in
# ═══════════════════════════════════════════════════════════════════════


def test_low_confidence_isolated_read_remains_held():
    """Item 3: a single sub-gate read, even with temporal_state present,
    cannot corroborate itself — run_length is only 1."""
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks(1, hr_value=85, hr_confidence=55.0, temporal_state=temporal_state)
    assert reading["hr"] == 75, "held at the seeded baseline"
    flag = _flag_for(flagged, "hr")
    assert flag["frameNote"].startswith("hr: OCR confidence")


def test_repeated_low_confidence_reading_corroborates_after_min_run():
    """Item 4: the policy under test. Confidence 55 (>= floor, < gate)
    repeated TEMPORAL_AGREEMENT_MIN_RUN times in a row confirms, flagged as
    temporal_corroboration, never silently as ai_high/ai_medium."""
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks(
        TEMPORAL_AGREEMENT_MIN_RUN, hr_value=85, hr_confidence=55.0, temporal_state=temporal_state
    )
    assert reading["hr"] == 85
    flag = _flag_for(flagged, "hr")
    assert "temporal corroboration" in flag["frameNote"]
    assert str(TEMPORAL_AGREEMENT_MIN_RUN) in flag["frameNote"]
    assert flag["severity"] == "warning", "accepted-but-flagged, same severity class as medium_confidence"


def test_one_short_of_min_run_still_holds():
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks(
        TEMPORAL_AGREEMENT_MIN_RUN - 1, hr_value=85, hr_confidence=55.0, temporal_state=temporal_state
    )
    assert reading["hr"] == 75
    flag = _flag_for(flagged, "hr")
    assert "temporal" not in flag["frameNote"]


def test_confidence_below_floor_never_corroborates_no_matter_how_long_the_run():
    """Below CONFIDENCE_TEMPORAL_FLOOR, M5.1's own oracle-crop evidence
    showed confidence carries almost no signal — repetition alone must not
    be enough."""
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks(
        TEMPORAL_AGREEMENT_MIN_RUN + 5, hr_value=85, hr_confidence=CONFIDENCE_TEMPORAL_FLOOR - 1,
        temporal_state=temporal_state,
    )
    assert reading["hr"] == 75
    flag = _flag_for(flagged, "hr")
    assert "temporal" not in flag["frameNote"]


def test_repeated_implausible_value_is_never_corroborated_into_confirmation():
    """Item 5 / 7 / 8: the dangerous case. A value that is repeated,
    consistent, and even clears the temporal confidence floor must still be
    rejected outright if it is physiologically implausible — range/jump
    checks run BEFORE the temporal branch and are never bypassed by it."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed({"hr": 74})
    last_reading = None
    for i in range(TEMPORAL_AGREEMENT_MIN_RUN + 3):
        raw = _reading(hr=9999, timestamp=T + i * TICK_MS)  # outside RANGE_BOUNDS["hr"]
        conf = _confidence(hr=60.0)
        last_reading, confirmed, flagged = reconcile(raw, conf, confirmed, temporal_state=temporal_state)
    assert last_reading["hr"] == 74, "an out-of-range value must never be confirmed, repeated or not"
    flag = _flag_for(flagged, "hr")
    assert flag["severity"] == "critical"


def test_repeated_but_wrong_within_plausible_range_is_flagged_not_hidden():
    """Explicit Phase 3 'dangerous case' probe: a repeated, physiologically
    PLAUSIBLE but (hypothetically) incorrect value at floor-clearing
    confidence WILL be accepted by this mechanism — that is the documented,
    deliberate tradeoff (this module has no access to ground truth and
    cannot distinguish genuine repeated signal from a systematic misread).
    The safety property under test is narrower and still holds: it is never
    silently presented as ai_high/ai_medium — it is always explicitly
    flagged as 'temporal_corroboration' with its real (sub-gate) confidence
    visible, so a clinician reviewing flagged items sees exactly how thin
    the evidence was."""
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks(
        TEMPORAL_AGREEMENT_MIN_RUN, hr_value=85, hr_confidence=45.0, temporal_state=temporal_state
    )
    assert reading["hr"] == 85  # accepted -- the documented tradeoff, not a claim of correctness
    flag = _flag_for(flagged, "hr")
    assert "temporal corroboration" in flag["frameNote"]
    assert "45" in flag["frameNote"], "the REAL sub-gate confidence must remain visible, never disguised as ai_medium/ai_high"


def test_extraction_failure_mid_run_resets_corroboration():
    """Item 6: a tracking/extraction failure surfaces as raw_value=None for
    that tick (app.pipeline.calibrated_roi's fail-closed contract). This
    must break an in-progress run rather than letting it survive — bad
    tracking must never increase trust."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed({"hr": 74})
    ticks = [(85, 55.0), (85, 55.0), (None, 0.0), (85, 55.0), (85, 55.0)]
    last_reading = None
    for i, (value, conf_val) in enumerate(ticks):
        raw = _reading(hr=value, timestamp=T + i * TICK_MS)
        conf = _confidence(hr=conf_val)
        last_reading, confirmed, flagged = reconcile(raw, conf, confirmed, temporal_state=temporal_state)
    # Only 2 consecutive real reads since the reset (indices 3, 4) -- one
    # short of TEMPORAL_AGREEMENT_MIN_RUN==3, so still held.
    assert last_reading["hr"] == 74
    assert temporal_state["hr"].run_length == 2


def test_session_reset_clears_temporal_evidence():
    """Item 9: a fresh initial_temporal_state() carries no memory of a prior
    connection's near-corroborated run."""
    old_state = initial_temporal_state()
    _run_ticks(TEMPORAL_AGREEMENT_MIN_RUN - 1, hr_value=85, hr_confidence=55.0, temporal_state=old_state)
    assert old_state["hr"].run_length == TEMPORAL_AGREEMENT_MIN_RUN - 1

    fresh_state = initial_temporal_state()
    assert fresh_state["hr"].run_length == 0
    reading, _updated, flagged = _run_ticks(1, hr_value=85, hr_confidence=55.0, temporal_state=fresh_state)
    assert reading["hr"] == 75, "a fresh session must not inherit the old session's near-complete run"


def test_different_sessions_do_not_share_confirmation_history():
    """Item 10: two independent temporal_state dicts (one per connection, as
    app.ws.vitals.send_loop constructs them) never interact."""
    session_a = initial_temporal_state()
    session_b = initial_temporal_state()
    _run_ticks(TEMPORAL_AGREEMENT_MIN_RUN, hr_value=85, hr_confidence=55.0, temporal_state=session_a)
    assert session_a["hr"].run_length == TEMPORAL_AGREEMENT_MIN_RUN
    assert session_b["hr"].run_length == 0, "session B must be completely unaffected by session A's history"


def test_alerts_still_fire_for_a_temporally_corroborated_critical_value():
    """Item 11: check_alerts() operates on the reconciled `reading` dict; a
    value confirmed via temporal corroboration is indistinguishable to it
    from any other confirmed value, so a genuinely critical corroborated
    reading must still alert."""
    from app.alerts.rules import check_alerts

    temporal_state = initial_temporal_state()
    reading, _updated, _flagged = _run_ticks(
        TEMPORAL_AGREEMENT_MIN_RUN, hr_value=35, hr_confidence=55.0, temporal_state=temporal_state
    )
    assert reading["hr"] == 35
    alerts = check_alerts(reading)
    assert any(a["vitalType"] == "hr" and a["severity"] == "critical" for a in alerts), \
        "a corroborated critically-low HR must still raise the existing critical-HR alert, unmodified"


def test_nibp_subfields_are_corroborated_independently():
    """NIBP's 3 sub-fields share one OCR confidence but are independent
    reconcile() fields -- corroboration must respect that, not conflate the
    three, exactly like every other field-level check in reconcile()."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed({"nibpSystolic": 120, "nibpDiastolic": 78, "nibpMean": 92})
    last_reading = None
    for i in range(TEMPORAL_AGREEMENT_MIN_RUN):
        raw = _reading(nibpSystolic=150, nibpDiastolic=78, nibpMean=92, timestamp=T + i * TICK_MS)
        conf = _confidence(nibp=55.0)
        last_reading, confirmed, flagged = reconcile(raw, conf, confirmed, temporal_state=temporal_state)
    assert last_reading["nibpSystolic"] == 150, "systolic repeated 3x at floor-clearing confidence -> corroborated"
    assert last_reading["nibpDiastolic"] == 78  # never changed -- was already the confirmed value
    assert last_reading["nibpMean"] == 92


# ═══════════════════════════════════════════════════════════════════════
# app.ws.vitals wiring: env var + send_loop auto-seeding
# ═══════════════════════════════════════════════════════════════════════


def test_temporal_corroboration_env_var_default_is_off(monkeypatch):
    from app.ws.vitals import _temporal_corroboration_enabled

    monkeypatch.delenv("TEMPORAL_CORROBORATION", raising=False)
    assert _temporal_corroboration_enabled() is False


def test_temporal_corroboration_env_var_on_enables_it(monkeypatch):
    from app.ws.vitals import _temporal_corroboration_enabled

    monkeypatch.setenv("TEMPORAL_CORROBORATION", "on")
    assert _temporal_corroboration_enabled() is True
    monkeypatch.setenv("TEMPORAL_CORROBORATION", "off")
    assert _temporal_corroboration_enabled() is False


class _FixedRepeatingSource(VitalsSource):
    """n identical low-confidence HR=85 ticks, matching this file's other
    helpers -- built the same way tests/test_ws_stream.py's own
    _FixedFrameSource is, so send_loop is exercised exactly as the real WS
    handler exercises it (no TestClient, no mocking of send_loop itself)."""

    def __init__(self, n):
        self._n = n

    async def stream(self):
        for i in range(self._n):
            reading = _reading(hr=85, timestamp=T + i * TICK_MS)
            yield Frame(reading=reading, per_vital_confidence=_confidence(hr=55.0), provenance="ai_low")


def test_send_loop_does_not_corroborate_when_feature_flag_is_off(monkeypatch):
    """Item 15/16 (isolation + backward-compatible envelopes): with the flag
    off (the default), send_loop must produce the exact pre-M5.4 envelope
    shape for a repeated low-confidence reading -- held, flagged as
    low_confidence, no 'temporal' text anywhere."""
    monkeypatch.delenv("TEMPORAL_CORROBORATION", raising=False)
    from app.ws.vitals import send_loop

    sent = []

    async def _send_json(payload):
        sent.append(payload)

    asyncio.run(send_loop(
        _send_json, _FixedRepeatingSource(TEMPORAL_AGREEMENT_MIN_RUN + 2), AlertThrottle(), deque(maxlen=50)
    ))

    readings = [m for m in sent if m["type"] == "reading"]
    assert all(r["reading"]["hr"] == 75 for r in readings), "must stay held throughout -- feature flag is off"
    flagged = [m for m in sent if m["type"] == "flagged" and m["flagged"]["vital"] == "hr"]
    assert flagged and all("temporal" not in f["flagged"]["frameNote"] for f in flagged)


def test_send_loop_persists_a_temporal_corroboration_flag_to_real_sqlite(monkeypatch):
    """Phase 6 persistence check: real app.db.repo + real SessionLocal (same
    scratch-DB pattern tests/test_persistence.py already establishes for
    send_loop), not a mock. Confirms a corroborated reading's flagged reason
    round-trips through actual SQLite, and that the reading eventually
    persists at the corroborated value -- not just that reconcile() computed
    the right in-memory dict."""
    monkeypatch.setenv("TEMPORAL_CORROBORATION", "on")
    from fastapi.testclient import TestClient

    from app.db import repo
    from app.db.session import SessionLocal
    from app.main import app
    from app.ws.vitals import send_loop

    client = TestClient(app)
    r = client.post("/api/sessions", json={"patientId": "PT-M54", "procedure": "Test", "anesthetist": "Dr. X"})
    assert r.status_code == 201
    session_id = r.json()["id"]

    sent = []

    async def _send_json(payload):
        sent.append(payload)

    asyncio.run(send_loop(
        _send_json, _FixedRepeatingSource(TEMPORAL_AGREEMENT_MIN_RUN + 1), AlertThrottle(), deque(maxlen=50),
        session_id=session_id, session_factory=SessionLocal,
    ))

    db = SessionLocal()
    try:
        persisted_readings = repo.list_readings(db, session_id)
        persisted_flagged = repo.list_flagged(db, session_id)
    finally:
        db.close()

    assert persisted_readings[-1]["hr"] == 85, "the corroborated value must be what actually persisted"
    corroborated_flags = [f for f in persisted_flagged if "temporal corroboration" in f.frame_note]
    assert corroborated_flags, "a real, persisted flagged row must record the corroboration reason"


def test_send_loop_corroborates_when_feature_flag_is_on(monkeypatch):
    monkeypatch.setenv("TEMPORAL_CORROBORATION", "on")
    from app.ws.vitals import send_loop

    sent = []

    async def _send_json(payload):
        sent.append(payload)

    asyncio.run(send_loop(
        _send_json, _FixedRepeatingSource(TEMPORAL_AGREEMENT_MIN_RUN + 2), AlertThrottle(), deque(maxlen=50)
    ))

    readings = [m["reading"]["hr"] for m in sent if m["type"] == "reading"]
    # Held for the first (MIN_RUN - 1) ticks, then confirmed once the run
    # clears the bar, and stays confirmed for every tick after.
    assert readings[: TEMPORAL_AGREEMENT_MIN_RUN - 1] == [75] * (TEMPORAL_AGREEMENT_MIN_RUN - 1)
    assert all(v == 85 for v in readings[TEMPORAL_AGREEMENT_MIN_RUN - 1 :])
