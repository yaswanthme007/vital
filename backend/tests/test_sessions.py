from fastapi.testclient import TestClient

from app.db import repo
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)


def _create_session(**overrides) -> dict:
    body = {
        "patientId": "PT-1",
        "patientAge": 45,
        "patientWeight": 70,
        "asa": 2,
        "procedure": "Laparoscopic Cholecystectomy",
        "anesthetist": "Dr. Priya Sharma",
    }
    body.update(overrides)
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_full_session_lifecycle_with_computed_vital_summary():
    session = _create_session()
    session_id = session["id"]
    assert session["status"] == "active"
    assert session["startTime"] > 0
    assert session["patient"] == {"id": "PT-1", "age": 45, "weight": 70, "asa": 2}

    r = client.get(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    assert r.json()["id"] == session_id

    r = client.post(f"/api/sessions/{session_id}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    r = client.post(f"/api/sessions/{session_id}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "active"

    r = client.post(f"/api/sessions/{session_id}/notes", json={"text": "Induction started", "category": "event"})
    assert r.status_code == 201
    note = r.json()
    assert note["text"] == "Induction started"
    assert note["category"] == "event"
    assert note["id"].startswith("NOTE-")

    # Seed real readings directly via the repo (this is what the WS stream
    # does per tick) so end_session computes a genuine vitalSummary.
    db = SessionLocal()
    for hr, spo2, etco2 in [(70, 98, 36), (74, 97, 38), (78, 95, 40)]:
        repo.save_reading(
            db,
            session_id,
            {"hr": hr, "spo2": spo2, "etco2": etco2, "timestamp": 1_700_000_000_000},
            confidence=95.0,
            provenance="ai_high",
        )
    db.close()

    r = client.post(f"/api/sessions/{session_id}/end")
    assert r.status_code == 200
    archived = r.json()
    assert archived["status"] == "completed"
    assert archived["endTime"] is not None
    assert archived["endTime"] >= archived["startTime"]
    assert len(archived["notes"]) == 1

    summary = archived["vitalSummary"]
    assert summary["avgHr"] == (70 + 74 + 78) / 3
    assert summary["minSpo2"] == 95
    assert summary["avgEtco2"] == (36 + 38 + 40) / 3
    assert summary["durationMin"] >= 0

    r = client.get("/api/sessions", params={"status": "completed"})
    assert r.status_code == 200
    archived_list = r.json()
    assert any(s["id"] == session_id for s in archived_list)
    assert all("vitalSummary" in s for s in archived_list)


def test_get_nonexistent_session_is_404():
    r = client.get("/api/sessions/NOPE")
    assert r.status_code == 404


def test_pause_nonexistent_session_is_404():
    r = client.post("/api/sessions/NOPE/pause")
    assert r.status_code == 404


# ─── alarm limits ─────────────────────────────────────────────────────────


def test_alarm_limits_default_and_update_persists():
    session = _create_session()
    session_id = session["id"]

    r = client.get(f"/api/sessions/{session_id}/alarm-limits")
    assert r.status_code == 200
    limits = r.json()
    assert len(limits) == 6
    by_vital = {limit["vitalType"]: limit for limit in limits}

    # Matches frontend-reference/src/types/vitals.ts's DEFAULT_ALARM_LIMITS exactly.
    assert by_vital["hr"] == {"vitalType": "hr", "highCritical": 130, "highWarning": 110, "lowWarning": 50, "lowCritical": 40}
    assert by_vital["spo2"] == {"vitalType": "spo2", "highCritical": None, "highWarning": 100, "lowWarning": 94, "lowCritical": 90}

    r = client.put(
        f"/api/sessions/{session_id}/alarm-limits/hr",
        json={"highCritical": 140, "highWarning": 120, "lowWarning": 45, "lowCritical": 35},
    )
    assert r.status_code == 200
    assert r.json() == {"vitalType": "hr", "highCritical": 140, "highWarning": 120, "lowWarning": 45, "lowCritical": 35}

    # Persisted: a fresh GET reflects the update, other vitals untouched.
    r = client.get(f"/api/sessions/{session_id}/alarm-limits/hr")
    assert r.json()["highCritical"] == 140
    r = client.get(f"/api/sessions/{session_id}/alarm-limits/spo2")
    assert r.json()["highWarning"] == 100


def test_alarm_limit_unknown_vital_type_is_422():
    session = _create_session()
    r = client.get(f"/api/sessions/{session['id']}/alarm-limits/not_a_vital")
    assert r.status_code == 422  # FastAPI Literal path-param validation


# ─── alerts ───────────────────────────────────────────────────────────────


def test_alerts_save_list_active_and_ack():
    session = _create_session()
    session_id = session["id"]

    db = SessionLocal()
    a1 = repo.save_alert(
        db,
        session_id,
        {"id": "ALERT-1-aaaa", "vitalType": "spo2", "severity": "critical", "message": "SpO2 CRITICALLY LOW", "value": 88, "unit": "%", "timestamp": 1_700_000_000_000, "acknowledged": False},
    )
    a2 = repo.save_alert(
        db,
        session_id,
        {"id": "ALERT-2-bbbb", "vitalType": "hr", "severity": "warning", "message": "Heart Rate Elevated", "value": 115, "unit": "bpm", "timestamp": 1_700_000_001_000, "acknowledged": False},
    )
    db.close()

    r = client.get(f"/api/sessions/{session_id}/alerts", params={"active": "true"})
    assert r.status_code == 200
    active = r.json()
    assert {a["id"] for a in active} == {a1.id, a2.id}
    assert all(a["acknowledged"] is False for a in active)

    r = client.post(f"/api/alerts/{a1.id}/ack")
    assert r.status_code == 200
    assert r.json()["acknowledged"] is True

    r = client.get(f"/api/sessions/{session_id}/alerts", params={"active": "true"})
    remaining_active = r.json()
    assert {a["id"] for a in remaining_active} == {a2.id}

    r = client.get(f"/api/sessions/{session_id}/alerts")
    all_alerts = r.json()
    assert {a["id"] for a in all_alerts} == {a1.id, a2.id}


def test_ack_nonexistent_alert_is_404():
    r = client.post("/api/alerts/NOPE/ack")
    assert r.status_code == 404


# --- readings (M5.7) --------------------------------------------------------


def test_get_readings_returns_persisted_rows_camelcase_with_source_and_status():
    session = _create_session()
    session_id = session["id"]

    db = SessionLocal()
    repo.save_reading(
        db, session_id,
        {"hr": 76, "timestamp": 1_700_000_000_000},
        confidence=95.0, provenance="ai_high",
        source="camera", field_status={"hr": "confirmed"},
    )
    repo.save_reading(
        db, session_id,
        {"hr": 78, "timestamp": 1_700_000_001_000},
        confidence=96.0, provenance="ai_high",
        source="camera", field_status={"hr": "confirmed"},
    )
    db.close()

    r = client.get(f"/api/sessions/{session_id}/readings")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert [row["hr"] for row in rows] == [76, 78]
    assert rows[0]["source"] == "camera"
    assert rows[0]["fieldStatus"] == {"hr": "confirmed"}
    assert rows[0]["timestamp"] == 1_700_000_000_000


def test_get_readings_since_returns_only_newer_rows():
    session = _create_session()
    session_id = session["id"]

    db = SessionLocal()
    for i, ts in enumerate([1_700_000_000_000, 1_700_000_001_000, 1_700_000_002_000]):
        repo.save_reading(db, session_id, {"hr": 70 + i, "timestamp": ts}, source="camera")
    db.close()

    r = client.get(f"/api/sessions/{session_id}/readings", params={"since": 1_700_000_000_000})
    assert r.status_code == 200
    rows = r.json()
    assert [row["timestamp"] for row in rows] == [1_700_000_001_000, 1_700_000_002_000]


def test_get_readings_limit_caps_the_oldest_first():
    session = _create_session()
    session_id = session["id"]

    db = SessionLocal()
    for i, ts in enumerate([1_700_000_000_000, 1_700_000_001_000, 1_700_000_002_000]):
        repo.save_reading(db, session_id, {"hr": 70 + i, "timestamp": ts}, source="camera")
    db.close()

    r = client.get(f"/api/sessions/{session_id}/readings", params={"limit": 2})
    assert r.status_code == 200
    rows = r.json()
    assert [row["timestamp"] for row in rows] == [1_700_000_000_000, 1_700_000_001_000]


def test_get_readings_for_unknown_session_is_404():
    r = client.get("/api/sessions/NOPE/readings")
    assert r.status_code == 404


def test_get_readings_for_a_completed_session_still_works():
    """Archive/Review read a session's timeline after it has ended -- the
    route must not require status=active."""
    session = _create_session()
    session_id = session["id"]

    db = SessionLocal()
    repo.save_reading(db, session_id, {"hr": 76, "timestamp": 1_700_000_000_000}, source="camera")
    db.close()

    r = client.post(f"/api/sessions/{session_id}/end")
    assert r.status_code == 200

    r = client.get(f"/api/sessions/{session_id}/readings")
    assert r.status_code == 200
    assert len(r.json()) == 1
