from fastapi.testclient import TestClient

from app.db import repo
from app.db.models import DrugEventRow
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)


def _create_session(**overrides) -> str:
    body = {"patientId": "PT-1", "procedure": "Test", "anesthetist": "Dr. Priya Sharma"}
    body.update(overrides)
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _log_drug(session_id: str, **overrides) -> dict:
    body = {
        "drugName": "Propofol",
        "dose": 150,
        "unit": "mg",
        "route": "IV",
        "entryMethod": "quick_preset",
        "enteredBy": "Dr. Priya Sharma",
    }
    body.update(overrides)
    r = client.post(f"/api/sessions/{session_id}/drugs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ─── logging + cumulative dose ─────────────────────────────────────────────


def test_second_propofol_dose_has_correct_cumulative_dose():
    session_id = _create_session()

    first = _log_drug(session_id, dose=150)
    assert first["cumulativeDose"] == 150
    assert first["drugName"] == "Propofol"

    second = _log_drug(session_id, dose=50)
    assert second["cumulativeDose"] == 200


def test_cumulative_dose_is_per_drug_name_not_global():
    session_id = _create_session()

    _log_drug(session_id, drugName="Propofol", dose=150)
    fentanyl = _log_drug(session_id, drugName="Fentanyl", dose=100, unit="mcg")

    assert fentanyl["cumulativeDose"] == 100, "different drug must have its own running total"


def test_log_drug_returns_full_drug_event_shape():
    session_id = _create_session()
    event = _log_drug(session_id, dose=50, unit="mg", route="IV", notes="slow push", isReversal=False)

    assert set(event.keys()) >= {
        "id",
        "sessionId",
        "drugName",
        "dose",
        "unit",
        "route",
        "rate",
        "rateUnit",
        "administeredAt",
        "recordedAt",
        "notes",
        "isReversal",
        "reversalOf",
        "entryMethod",
        "enteredBy",
        "cumulativeDose",
    }
    assert event["sessionId"] == session_id
    assert event["notes"] == "slow push"


def test_log_drug_for_unknown_session_is_404():
    r = client.post(
        "/api/sessions/NOPE/drugs",
        json={"drugName": "Propofol", "dose": 150, "unit": "mg", "route": "IV", "enteredBy": "Dr. X"},
    )
    assert r.status_code == 404


# ─── list ────────────────────────────────────────────────────────────────


def test_get_drugs_ordered_by_administered_at():
    session_id = _create_session()
    _log_drug(session_id, drugName="Fentanyl", dose=100, unit="mcg", administeredAt=1_700_000_002_000)
    _log_drug(session_id, drugName="Propofol", dose=150, administeredAt=1_700_000_001_000)
    _log_drug(session_id, drugName="Rocuronium", dose=50, administeredAt=1_700_000_003_000)

    r = client.get(f"/api/sessions/{session_id}/drugs")
    assert r.status_code == 200
    names = [e["drugName"] for e in r.json()]
    assert names == ["Propofol", "Fentanyl", "Rocuronium"]


def test_get_drugs_for_unknown_session_is_404():
    assert client.get("/api/sessions/NOPE/drugs").status_code == 404


# ─── drugs_count on the session ────────────────────────────────────────────


def test_drugs_count_increments_on_session():
    session_id = _create_session()
    assert client.get(f"/api/sessions/{session_id}").json()["drugsCount"] == 0

    _log_drug(session_id, dose=150)
    assert client.get(f"/api/sessions/{session_id}").json()["drugsCount"] == 1

    _log_drug(session_id, dose=50)
    assert client.get(f"/api/sessions/{session_id}").json()["drugsCount"] == 2


# ─── presets ────────────────────────────────────────────────────────────


def test_presets_endpoint_returns_the_list():
    r = client.get("/api/drugs/presets")
    assert r.status_code == 200
    presets = r.json()
    assert len(presets) >= 16
    names = {p["drugName"] for p in presets}
    for expected in (
        "Propofol",
        "Thiopental",
        "Ketamine",
        "Etomidate",
        "Fentanyl",
        "Morphine",
        "Remifentanil",
        "Rocuronium",
        "Suxamethonium",
        "Sugammadex",
        "Sevoflurane",
        "Isoflurane",
        "Ondansetron",
        "Dexamethasone",
        "Atropine",
        "Ephedrine",
        "Neostigmine",
    ):
        assert expected in names, f"{expected} missing from presets"

    sugammadex = next(p for p in presets if p["drugName"] == "Sugammadex")
    assert sugammadex["isReversal"] is True
    assert sugammadex["reversalOf"] == "Rocuronium"


# ─── recent drugs by user ───────────────────────────────────────────────


def test_recent_drugs_returns_distinct_names_most_recent_first():
    session_id = _create_session()
    _log_drug(session_id, drugName="Propofol", dose=150, enteredBy="Dr. Z", administeredAt=1_700_000_001_000)
    _log_drug(session_id, drugName="Fentanyl", dose=100, unit="mcg", enteredBy="Dr. Z", administeredAt=1_700_000_002_000)
    _log_drug(session_id, drugName="Propofol", dose=50, enteredBy="Dr. Z", administeredAt=1_700_000_003_000)  # repeat name

    r = client.get("/api/drugs/recent", params={"user": "Dr. Z"})
    assert r.status_code == 200
    assert r.json() == ["Propofol", "Fentanyl"]  # distinct, most-recently-administered first


def test_recent_drugs_for_unknown_user_is_empty_list():
    r = client.get("/api/drugs/recent", params={"user": "Nobody"})
    assert r.status_code == 200
    assert r.json() == []


# ─── correction window ─────────────────────────────────────────────────


def test_correct_within_window_succeeds_and_records_history():
    session_id = _create_session()
    event = _log_drug(session_id, dose=150)

    r = client.patch(f"/api/drug-events/{event['id']}", json={"dose": 175, "author": "Dr. Priya Sharma"})
    assert r.status_code == 200
    assert r.json()["dose"] == 175

    db = SessionLocal()
    history = repo.list_drug_corrections(db, event["id"])
    db.close()

    assert len(history) == 1
    assert history[0]["field"] == "dose"
    assert history[0]["prevValue"] == 150
    assert history[0]["newValue"] == 175
    assert history[0]["author"] == "Dr. Priya Sharma"


def test_correct_cascades_cumulative_dose_recompute():
    session_id = _create_session()
    first = _log_drug(session_id, dose=150)
    _log_drug(session_id, dose=50)  # cumulative 200

    client.patch(f"/api/drug-events/{first['id']}", json={"dose": 175})

    drugs = client.get(f"/api/sessions/{session_id}/drugs").json()
    doses = {d["id"]: d["cumulativeDose"] for d in drugs}
    assert doses[first["id"]] == 175
    assert list(doses.values())[-1] == 225, "later event's cumulative total must reflect the corrected earlier dose"


def test_correct_after_five_minutes_is_rejected():
    session_id = _create_session()
    event = _log_drug(session_id, dose=150)

    # Simulate the correction window having elapsed.
    db = SessionLocal()
    row = db.get(DrugEventRow, event["id"])
    row.recorded_at -= 6 * 60 * 1000
    db.commit()
    db.close()

    r = client.patch(f"/api/drug-events/{event['id']}", json={"dose": 999})
    assert r.status_code == 409

    # And the dose must be unchanged.
    drugs = client.get(f"/api/sessions/{session_id}/drugs").json()
    assert drugs[0]["dose"] == 150


def test_correct_unknown_drug_event_is_404():
    r = client.patch("/api/drug-events/NOPE", json={"dose": 1})
    assert r.status_code == 404
