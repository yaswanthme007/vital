from fastapi.testclient import TestClient

from app.db import repo
from app.db.models import SessionNoteRow
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)

INTERVAL_MS = 5 * 60 * 1000


def _create_session(**overrides) -> dict:
    body = {"patientId": "PT-1", "procedure": "Appendectomy", "anesthetist": "Dr. Priya Sharma"}
    body.update(overrides)
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _save_reading(session_id: str, timestamp: int, hr: float) -> None:
    db = SessionLocal()
    repo.save_reading(
        db,
        session_id,
        {
            "hr": hr,
            "spo2": 98,
            "nibpSystolic": 120,
            "nibpDiastolic": 78,
            "nibpMean": 92,
            "etco2": 38,
            "temp": 36.8,
            "rr": 14,
            "timestamp": timestamp,
        },
    )
    db.close()


def test_chart_has_periodic_rows_with_nearest_vitals_and_event_overlays():
    session = _create_session()
    sid = session["id"]
    start = int(session["startTime"])

    _save_reading(sid, start, hr=70)
    _save_reading(sid, start + INTERVAL_MS, hr=80)
    _save_reading(sid, start + 2 * INTERVAL_MS, hr=90)

    drug = client.post(
        f"/api/sessions/{sid}/drugs",
        json={
            "drugName": "Propofol",
            "dose": 150,
            "unit": "mg",
            "route": "IV",
            "enteredBy": "Dr. Priya Sharma",
            "entryMethod": "quick_preset",
            "administeredAt": start + 100_000,  # nearer to mark 0 than mark 1
        },
    )
    assert drug.status_code == 201, drug.text

    note = client.post(f"/api/sessions/{sid}/notes", json={"text": "Extubated", "category": "event"})
    assert note.status_code == 201, note.text
    # add_note timestamps with time.time(), so backdate it directly to land near mark 2
    # (nearest-mark bucketing needs a controlled timestamp, same technique test_drugs.py
    # uses to simulate an elapsed correction window).
    db = SessionLocal()
    note_row = db.get(SessionNoteRow, note.json()["id"])
    note_row.timestamp = start + 2 * INTERVAL_MS - 50_000
    db.commit()
    db.close()

    r = client.get(f"/api/sessions/{sid}/chart", params={"intervalMinutes": 5})
    assert r.status_code == 200
    chart = r.json()

    assert chart["intervalMinutes"] == 5
    rows = chart["rows"]
    assert len(rows) == 3
    assert [row["timestamp"] for row in rows] == [start, start + INTERVAL_MS, start + 2 * INTERVAL_MS]
    assert [row["hr"] for row in rows] == [70, 80, 90]

    assert len(rows[0]["events"]) == 1
    assert rows[0]["events"][0]["type"] == "drug"
    assert rows[0]["events"][0]["drugName"] == "Propofol"
    assert rows[0]["events"][0]["cumulativeDose"] == 150

    assert rows[1]["events"] == []

    assert len(rows[2]["events"]) == 1
    assert rows[2]["events"][0]["type"] == "note"
    assert rows[2]["events"][0]["text"] == "Extubated"

    summary = chart["vitalSummary"]
    assert summary["avgHr"] == 80.0
    assert summary["minSpo2"] == 98.0
    assert summary["avgEtco2"] == 38.0
    assert summary["durationMin"] == 10.0


def test_chart_for_unknown_session_is_404():
    r = client.get("/api/sessions/NOPE/chart")
    assert r.status_code == 404


def test_chart_with_no_readings_yet_has_a_single_all_null_row():
    session = _create_session()
    r = client.get(f"/api/sessions/{session['id']}/chart")
    assert r.status_code == 200
    chart = r.json()
    assert len(chart["rows"]) == 1
    assert chart["rows"][0]["hr"] is None
    assert chart["vitalSummary"]["avgHr"] == 0.0


def test_chart_matches_each_field_to_its_own_nearest_observation():
    """M5.7: post-reconcile-gating persistence writes sparse rows (only the
    fields a tick actually confirmed). A mark's HR and SpO2 must each be
    nearest-matched independently, not both forced to whatever single row
    happens to be nearest overall — otherwise a mark can show a stale/null
    SpO2 even though a SpO2 observation exists closer in time than the row
    that supplied HR."""
    session = _create_session()
    sid = session["id"]
    start = int(session["startTime"])

    db = SessionLocal()
    # Mark 0 (timestamp=start): only HR was confirmed this tick.
    repo.save_reading(db, sid, {"hr": 70, "timestamp": start}, source="camera")
    # A SpO2-only observation much closer to mark 0 than mark 1 — the
    # pre-M5.7 "one nearest row" approach would never surface this value at
    # mark 0, because the row nearest to mark 0 overall is the HR-only row
    # immediately above.
    repo.save_reading(db, sid, {"spo2": 97, "timestamp": start + 10_000}, source="camera")
    # Mark 1 (timestamp=start+INTERVAL_MS): only HR confirmed again.
    repo.save_reading(db, sid, {"hr": 80, "timestamp": start + INTERVAL_MS}, source="camera")
    db.close()

    r = client.get(f"/api/sessions/{sid}/chart", params={"intervalMinutes": 5})
    assert r.status_code == 200
    rows = r.json()["rows"]

    assert rows[0]["hr"] == 70
    assert rows[0]["spo2"] == 97, "SpO2 must come from its own nearest observation, not a row missing it"
    assert rows[1]["hr"] == 80
    # The SpO2 observation is closer to mark 0 (10s away) than mark 1
    # (INTERVAL_MS - 10s away), so it must NOT also be attributed to mark 1.
    assert rows[1]["spo2"] == 97
