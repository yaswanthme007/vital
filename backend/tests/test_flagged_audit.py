import asyncio
from collections import deque

from fastapi.testclient import TestClient

from app.alerts.rules import AlertThrottle
from app.db import repo
from app.db.session import SessionLocal
from app.main import app
from app.sources.base import Frame, VitalsSource
from app.ws.vitals import send_loop

client = TestClient(app)


def _create_session(**overrides) -> str:
    body = {"patientId": "PT-1", "procedure": "Test", "anesthetist": "Dr. Priya Sharma"}
    body.update(overrides)
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


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
        "timestamp": 1_700_000_000_000,
    }
    base.update(overrides)
    return base


def _confidence(**overrides):
    base = {v: 95.0 for v in ("hr", "spo2", "nibp", "etco2", "temp", "rr")}
    base.update(overrides)
    return base


class _FixedFrameSource(VitalsSource):
    def __init__(self, frames):
        self._frames = frames

    async def stream(self):
        for frame in self._frames:
            yield frame


class _Sink:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def _stream_one_bad_reading(session_id: str, **reading_overrides) -> dict:
    """Drives one implausible reading through the real send_loop (with
    persistence enabled) and returns the resulting 'flagged' envelope."""
    frame = Frame(reading=_reading(**reading_overrides), per_vital_confidence=_confidence(), provenance="ai_high")
    sink = _Sink()
    asyncio.run(
        send_loop(
            sink.send_json,
            _FixedFrameSource([frame]),
            AlertThrottle(),
            deque(maxlen=10),
            session_id=session_id,
            session_factory=SessionLocal,
        )
    )
    return next(m for m in sink.messages if m["type"] == "flagged")


# ─── confirm / correct / dismiss + audit ────────────────────────────────────


def test_confirm_adopts_suggested_value_and_appends_audit():
    session_id = _create_session()
    flagged_id = _stream_one_bad_reading(session_id, temp=6.8)["flagged"]["id"]

    r = client.post(f"/api/flagged/{flagged_id}/confirm")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "corrected"
    assert body["correctedValue"] == "36.8"  # the system's own suggestedValue, adopted as-is

    audit = client.get(f"/api/sessions/{session_id}/audit").json()
    assert len(audit) == 1
    assert audit[0]["action"] == "human_correct"
    assert audit[0]["value"] == "36.8"
    assert audit[0]["prevValue"] == "6.8"
    assert audit[0]["author"] == "Dr. Priya Sharma"  # auto-resolved from the session


def test_correct_uses_supplied_value_and_appends_audit():
    session_id = _create_session()
    flagged_id = _stream_one_bad_reading(session_id, hr=999)["flagged"]["id"]

    r = client.post(f"/api/flagged/{flagged_id}/correct", json={"correctedValue": "78", "author": "Dr. Custom"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "corrected"
    assert body["correctedValue"] == "78"

    audit = client.get(f"/api/sessions/{session_id}/audit").json()
    assert len(audit) == 1
    assert audit[0]["action"] == "human_correct"
    assert audit[0]["value"] == "78"
    assert audit[0]["author"] == "Dr. Custom"


def test_dismiss_leaves_ai_value_and_appends_audit():
    session_id = _create_session()
    flagged_id = _stream_one_bad_reading(session_id, spo2=40)["flagged"]["id"]  # < 50 -> out of range

    r = client.post(f"/api/flagged/{flagged_id}/dismiss")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "dismissed"
    assert body["correctedValue"] is None

    audit = client.get(f"/api/sessions/{session_id}/audit").json()
    assert len(audit) == 1
    assert audit[0]["action"] == "human_dismiss"
    assert audit[0]["value"] == "40"
    assert audit[0]["prevValue"] is None


def test_audit_is_append_only_across_two_separate_actions():
    """Two separate corrections/dismissals in the same session must each add
    a NEW entry — never mutate or remove a prior one."""
    session_id = _create_session()

    flag1 = _stream_one_bad_reading(session_id, temp=6.8)["flagged"]["id"]
    assert client.post(f"/api/flagged/{flag1}/correct", json={"correctedValue": "36.9"}).status_code == 200

    audit_after_first = client.get(f"/api/sessions/{session_id}/audit").json()
    assert len(audit_after_first) == 1
    first_entry = audit_after_first[0]

    flag2 = _stream_one_bad_reading(session_id, hr=999, timestamp=1_700_000_005_000)["flagged"]["id"]
    assert client.post(f"/api/flagged/{flag2}/dismiss").status_code == 200

    audit_after_second = client.get(f"/api/sessions/{session_id}/audit").json()
    assert len(audit_after_second) == 2, "a new action must ADD an entry, not replace one"
    # The original entry is present, unchanged, byte-for-byte.
    assert audit_after_second[0] == first_entry
    assert audit_after_second[1]["action"] == "human_dismiss"


def test_confirm_correct_dismiss_on_unknown_flagged_id_is_404():
    assert client.post("/api/flagged/NOPE/confirm").status_code == 404
    assert client.post("/api/flagged/NOPE/correct", json={"correctedValue": "1"}).status_code == 404
    assert client.post("/api/flagged/NOPE/dismiss").status_code == 404


# ─── GET flagged / audit ────────────────────────────────────────────────────


def test_get_flagged_list_and_status_filter():
    session_id = _create_session()
    flag1 = _stream_one_bad_reading(session_id, temp=6.8)["flagged"]["id"]
    flag2 = _stream_one_bad_reading(session_id, hr=999, timestamp=1_700_000_005_000)["flagged"]["id"]
    client.post(f"/api/flagged/{flag1}/dismiss")

    all_flagged = client.get(f"/api/sessions/{session_id}/flagged").json()
    assert {f["id"] for f in all_flagged} == {flag1, flag2}

    pending_only = client.get(f"/api/sessions/{session_id}/flagged", params={"status": "pending"}).json()
    assert {f["id"] for f in pending_only} == {flag2}


def test_get_flagged_and_audit_for_unknown_session_is_404():
    assert client.get("/api/sessions/NOPE/flagged").status_code == 404
    assert client.get("/api/sessions/NOPE/audit").status_code == 404


# ─── orphan-readings gap (S7 regression, closed this session) ──────────────


def test_ws_with_unknown_session_id_creates_no_reading_rows():
    with client.websocket_connect("/ws/vitals/DOES-NOT-EXIST-ANYWHERE?source=synthetic&interval=0.02&seed=1") as ws:
        for _ in range(5):
            ws.receive_json()

    db = SessionLocal()
    readings = repo.list_readings(db, "DOES-NOT-EXIST-ANYWHERE")
    db.close()
    assert readings == []


def test_ws_stops_persisting_once_session_is_paused():
    session_id = _create_session()

    with client.websocket_connect(f"/ws/vitals/{session_id}?source=synthetic&interval=0.02&seed=2") as ws:
        for _ in range(3):
            ws.receive_json()

    assert client.post(f"/api/sessions/{session_id}/pause").status_code == 200

    with client.websocket_connect(f"/ws/vitals/{session_id}?source=synthetic&interval=0.02&seed=2") as ws:
        for _ in range(3):
            ws.receive_json()

    db = SessionLocal()
    readings = repo.list_readings(db, session_id)
    db.close()
    assert len(readings) == 3, "no new rows should be written once the session is no longer active"
