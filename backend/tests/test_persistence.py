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
