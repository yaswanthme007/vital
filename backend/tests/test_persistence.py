import asyncio
from collections import deque

from fastapi.testclient import TestClient

from app.alerts.rules import AlertThrottle
from app.db import repo
from app.db.session import SessionLocal
from app.main import app
from app.sources.base import Frame, VitalsSource
from app.sources.replay import VITALS
from app.ws.vitals import send_loop

client = TestClient(app)


def _create_session() -> str:
    r = client.post("/api/sessions", json={"patientId": "PT-1", "procedure": "Test", "anesthetist": "Dr. X"})
    assert r.status_code == 201
    return r.json()["id"]


def test_stream_persists_readings_in_timestamp_order():
    session_id = _create_session()

    with client.websocket_connect(f"/ws/vitals/{session_id}?source=synthetic&interval=0.02&seed=3") as ws:
        received = []
        for _ in range(6):
            msg = ws.receive_json()
            assert msg["type"] == "reading"
            received.append(msg["reading"])

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()

    assert len(persisted) == 6
    timestamps = [r["timestamp"] for r in persisted]
    assert timestamps == sorted(timestamps)
    # Persisted values match what was actually streamed, same order.
    assert [r["hr"] for r in persisted] == [r["hr"] for r in received]
    assert persisted[0]["provenance"] == "ai_high"
    assert persisted[0]["confidence"] == 100.0
    assert persisted[0]["perVitalConfidence"]["hr"] == 100.0


def test_stream_persistence_is_isolated_per_session():
    session_a = _create_session()
    session_b = _create_session()

    with client.websocket_connect(f"/ws/vitals/{session_a}?source=synthetic&interval=0.02&seed=1") as ws:
        for _ in range(3):
            ws.receive_json()

    db = SessionLocal()
    readings_a = repo.list_readings(db, session_a)
    readings_b = repo.list_readings(db, session_b)
    db.close()

    assert len(readings_a) == 3
    assert len(readings_b) == 0


def test_ending_session_computes_summary_from_stream_persisted_readings():
    session_id = _create_session()

    with client.websocket_connect(f"/ws/vitals/{session_id}?source=synthetic&interval=0.02&seed=5") as ws:
        readings = [ws.receive_json()["reading"] for _ in range(5)]

    r = client.post(f"/api/sessions/{session_id}/end")
    assert r.status_code == 200
    summary = r.json()["vitalSummary"]

    expected_avg_hr = sum(r["hr"] for r in readings) / len(readings)
    assert summary["avgHr"] == expected_avg_hr
    assert summary["minSpo2"] == min(r["spo2"] for r in readings)


class _FixedFrameSource(VitalsSource):
    def __init__(self, frames):
        self._frames = frames

    async def stream(self):
        for frame in self._frames:
            yield frame


def test_db_error_during_persistence_does_not_kill_the_stream():
    """Every save_reading call fails — the stream must still deliver all
    frames to the client rather than dying mid-tick."""
    reading = {
        "hr": 72,
        "spo2": 98,
        "nibpSystolic": 120,
        "nibpDiastolic": 78,
        "nibpMean": 92,
        "etco2": 38,
        "temp": 36.8,
        "rr": 14,
        "timestamp": 1_700_000_000_000,
    }
    confidence = {v: 100.0 for v in VITALS}
    frames = [Frame(reading=reading, per_vital_confidence=confidence, provenance="ai_high") for _ in range(5)]

    class _Sink:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            self.messages.append(data)

    def _broken_session_factory():
        raise RuntimeError("simulated DB connection failure")

    sink = _Sink()
    asyncio.run(
        send_loop(
            sink.send_json,
            _FixedFrameSource(frames),
            AlertThrottle(),
            deque(maxlen=10),
            session_id="SOME-SESSION",
            session_factory=_broken_session_factory,
        )
    )

    reading_msgs = [m for m in sink.messages if m["type"] == "reading"]
    assert len(reading_msgs) == 5


# --- M5.7: camera-only confirmed/gated persistence + confirmed-only alerts --
#
# The tests above (test_stream_persists_readings_in_timestamp_order etc.)
# cover source=synthetic, whose persistence is deliberately UNCHANGED by
# M5.7 (see send_loop's source_tag docstring) -- every one of them keeps
# passing untouched. These cover the NEW behaviour, scoped to
# source_tag="camera": only genuinely confirmed fields are ever persisted,
# gated by _PersistenceGate's >=1s-or-changed cadence, and alerts never fire
# from a held value on any source.


class _Sink:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def _vitals_confidence(**overrides):
    base = {v: 0.0 for v in VITALS}
    base.update(overrides)
    return base


def test_camera_all_held_tick_persists_no_fake_observation_row():
    """Every field unreadable this tick -- e.g. the monitor is occluded, or
    layout tracking withheld everything. Must write NOTHING, not a row full
    of held/baseline values masquerading as an observation."""
    session_id = _create_session()
    reading = {f: None for f in ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr")}
    reading["timestamp"] = 1_700_000_000_000
    frames = [Frame(reading=reading, per_vital_confidence=_vitals_confidence(), provenance="ai_low")]

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        session_id=session_id, session_factory=SessionLocal, source_tag="camera",
    ))

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()
    assert persisted == []

    reading_msg = next(m for m in sink.messages if m["type"] == "reading")
    # M5.8: 'unknown', not 'held' -- nothing has ever been confirmed for this
    # session, so there is no earlier observation to hold, and the reading
    # itself is null rather than a DEFAULT_BASELINE placeholder.
    assert reading_msg["fieldStatus"]["hr"] == "unknown"
    assert reading_msg["reading"]["hr"] is None


def test_camera_partial_confirmation_persists_only_the_confirmed_fields():
    session_id = _create_session()

    def _reading(t):
        return {
            "hr": 76, "spo2": None, "nibpSystolic": None, "nibpDiastolic": None,
            "nibpMean": None, "etco2": None, "temp": None, "rr": None, "timestamp": t,
        }

    # M5.8: two frames, because one frame confirms nothing on the camera
    # path any more (app.validation.live_corroboration). Both read the same
    # HR, so the second one corroborates the first.
    frames = [
        Frame(reading=_reading(1_700_000_000_000), per_vital_confidence=_vitals_confidence(hr=95.0), provenance="ai_high"),
        Frame(reading=_reading(1_700_000_000_400), per_vital_confidence=_vitals_confidence(hr=95.0), provenance="ai_high"),
    ]

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        session_id=session_id, session_factory=SessionLocal, source_tag="camera",
    ))

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()

    assert len(persisted) == 1
    row = persisted[0]
    assert row["hr"] == 76
    assert row["spo2"] is None
    assert row["nibpSystolic"] is None
    assert row["source"] == "camera"
    assert row["fieldStatus"]["hr"] == "confirmed"
    assert row["fieldStatus"]["spo2"] == "unknown"


def test_camera_first_frame_alone_never_persists_an_observation():
    """M5.8 regression guard. ONE frame, maximum confidence, a perfectly
    plausible value -- and still nothing is written, because a single frame
    is exactly the evidence that produced the demo recording's SpO2
    92/94/96/97/99, EtCO2 4 and RR 42 ledger rows. Corroboration is not an
    optimization here; it is the acceptance rule."""
    session_id = _create_session()
    reading = {
        "hr": 76, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78,
        "nibpMean": 92, "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1_700_000_000_000,
    }
    frames = [Frame(reading=reading, per_vital_confidence={v: 99.0 for v in VITALS}, provenance="ai_high")]

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        session_id=session_id, session_factory=SessionLocal, source_tag="camera",
    ))

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()
    assert persisted == []


def test_camera_transient_ocr_error_never_becomes_an_observation():
    """M5.8 regression guard for the "never write a bad OCR guess"
    requirement: a value the camera reads exactly once, between agreeing
    reads of the true value, must never reach the ledger. This is the
    literal 12 -> 42 -> 12 scenario from the real demo (RR read 42 once
    while the monitor showed 12)."""
    session_id = _create_session()
    T = 1_700_000_000_000

    def _reading(rr, t):
        return {
            "hr": None, "spo2": None, "nibpSystolic": None, "nibpDiastolic": None,
            "nibpMean": None, "etco2": None, "temp": None, "rr": rr, "timestamp": t,
        }

    conf = _vitals_confidence(rr=96.0)
    frames = [
        Frame(reading=_reading(12, T), per_vital_confidence=conf, provenance="ai_high"),
        Frame(reading=_reading(42, T + 1000), per_vital_confidence=conf, provenance="ai_high"),
        Frame(reading=_reading(12, T + 2000), per_vital_confidence=conf, provenance="ai_high"),
        Frame(reading=_reading(12, T + 3000), per_vital_confidence=conf, provenance="ai_high"),
    ]

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        session_id=session_id, session_factory=SessionLocal, source_tag="camera",
    ))

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()

    assert persisted, "the genuine value 12 must still be observed"
    assert [r["rr"] for r in persisted] == [12] * len(persisted)


def test_camera_cadence_gate_batches_by_time_or_change():
    session_id = _create_session()
    T = 1_700_000_000_000

    def _reading(hr, t):
        return {
            "hr": hr, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78,
            "nibpMean": 92, "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": t,
        }

    confidence = {v: 95.0 for v in VITALS}
    # M5.8: values now need a corroborating second frame before they can be
    # confirmed at all, so the sequence is written to exercise the CADENCE
    # gate on top of that: 70 confirms on the 2nd frame (first write), the
    # 3rd frame re-confirms the SAME 70 only 100ms later (gate withholds --
    # nothing changed and <1s elapsed), then 71 confirms on the 5th frame
    # (a changed value, written immediately regardless of the timer).
    frames = [
        Frame(reading=_reading(70, T), per_vital_confidence=confidence, provenance="ai_high"),
        Frame(reading=_reading(70, T + 200), per_vital_confidence=confidence, provenance="ai_high"),
        Frame(reading=_reading(70, T + 300), per_vital_confidence=confidence, provenance="ai_high"),
        Frame(reading=_reading(71, T + 400), per_vital_confidence=confidence, provenance="ai_high"),
        Frame(reading=_reading(71, T + 500), per_vital_confidence=confidence, provenance="ai_high"),
    ]

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        session_id=session_id, session_factory=SessionLocal, source_tag="camera",
    ))

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()

    assert [r["hr"] for r in persisted] == [70, 71]
    assert [r["timestamp"] for r in persisted] == [T + 200, T + 500]


def test_camera_source_tag_is_recorded_on_every_persisted_row():
    session_id = _create_session()
    reading = {
        "hr": 76, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78,
        "nibpMean": 92, "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1_700_000_000_000,
    }
    confidence = {v: 95.0 for v in VITALS}
    # Two frames: M5.8 needs a corroborating read before anything is written.
    frames = [
        Frame(reading=reading, per_vital_confidence=confidence, provenance="ai_high"),
        Frame(reading={**reading, "timestamp": reading["timestamp"] + 400},
              per_vital_confidence=confidence, provenance="ai_high"),
    ]

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        session_id=session_id, session_factory=SessionLocal, source_tag="camera",
    ))

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()
    assert persisted[0]["source"] == "camera"


def test_synthetic_source_persistence_is_unchanged_full_row_every_tick():
    """Non-camera source_tag (here: omitted entirely, matching every pre-
    M5.7 direct caller) must behave byte-for-byte as before -- the full
    reconciled reading, unconditionally, every persist-eligible tick. No
    gate, no confirmed-only filtering."""
    session_id = _create_session()
    T = 1_700_000_000_000
    confidence = {v: 95.0 for v in VITALS}

    def _reading(hr, t):
        return {
            "hr": hr, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78,
            "nibpMean": 92, "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": t,
        }

    frames = [
        Frame(reading=_reading(70, T), per_vital_confidence=confidence, provenance="ai_high"),
        Frame(reading=_reading(70, T + 50), per_vital_confidence=confidence, provenance="ai_high"),
    ]

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        session_id=session_id, session_factory=SessionLocal,
    ))

    db = SessionLocal()
    persisted = repo.list_readings(db, session_id)
    db.close()
    assert len(persisted) == 2
    assert persisted[0]["source"] is None


def test_alerts_never_fire_from_a_held_value():
    """A field held at a stale-but-still-alarm-worthy prior CONFIRMED value
    must not re-trigger a clinical alert on a tick where OCR simply failed
    to read it -- only a genuine new confirmation may raise an alert. Not
    camera-specific: this is a general reconcile-to-alert correctness fix."""
    from app.validation.reconcile import FieldState, initial_confirmed_state

    reading = {
        "hr": 72, "spo2": None, "nibpSystolic": 120, "nibpDiastolic": 78,
        "nibpMean": 92, "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1_700_000_000_000,
    }
    confidence = _vitals_confidence(hr=100.0, spo2=0.0, nibp=100.0, etco2=100.0, temp=100.0, rr=100.0)
    frames = [Frame(reading=reading, per_vital_confidence=confidence, provenance="ai_low")]

    confirmed_state = initial_confirmed_state(reading["timestamp"] - 5_000)
    confirmed_state["spo2"] = FieldState(value=92, timestamp=reading["timestamp"] - 5_000)

    sink = _Sink()
    asyncio.run(send_loop(
        sink.send_json, _FixedFrameSource(frames), AlertThrottle(), deque(maxlen=10),
        confirmed_state=confirmed_state,
    ))

    alert_msgs = [m for m in sink.messages if m["type"] == "alert"]
    assert alert_msgs == []
