import base64
import os
import re
import zlib

import pytest
from fastapi.testclient import TestClient

from app.db import repo
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)


def _create_session(**overrides) -> dict:
    body = {"patientId": "PT-1", "procedure": "Appendectomy", "anesthetist": "Dr. Priya Sharma"}
    body.update(overrides)
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _end(session_id: str) -> None:
    r = client.post(f"/api/sessions/{session_id}/end")
    assert r.status_code == 200, r.text


def _sign(session_id: str, author: str = "Dr. Priya Sharma", method: str = "pin"):
    return client.post(f"/api/sessions/{session_id}/sign", json={"author": author, "signatureMethod": method})


def _pdf_text(pdf_bytes: bytes) -> str:
    """ReportLab compresses content streams (ASCII85 + Flate by default), so
    a plain substring search over the raw PDF bytes never finds anything --
    decode every stream object with whichever filter combination works."""
    text = ""
    for raw in re.findall(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.DOTALL):
        for decode in (
            lambda b: zlib.decompress(base64.a85decode(b.strip(), adobe=True)),
            lambda b: zlib.decompress(b),
            lambda b: b,
        ):
            try:
                text += decode(raw).decode("latin1", errors="ignore")
                break
            except Exception:
                continue
    return text


# ─── sign ───────────────────────────────────────────────────────────────


def test_sign_on_non_completed_session_is_409():
    session = _create_session()
    r = _sign(session["id"])
    assert r.status_code == 409
    assert "completed" in r.json()["detail"].lower()


def test_sign_on_completed_session_succeeds():
    session = _create_session()
    _end(session["id"])

    r = _sign(session["id"], author="Dr. Priya Sharma", method="pin")
    assert r.status_code == 200, r.text
    signed = r.json()

    assert signed["signedAt"] is not None
    assert signed["signedBy"] == "Dr. Priya Sharma"
    assert signed["signatureMethod"] == "pin"
    assert signed["status"] == "completed"  # no "locked" status in the frontend contract
    assert signed["pdfUrl"] == f"/api/sessions/{session['id']}/report.pdf"


def test_resigning_a_signed_session_is_409():
    session = _create_session()
    _end(session["id"])
    assert _sign(session["id"]).status_code == 200

    r = _sign(session["id"])
    assert r.status_code == 409
    assert "locked" in r.json()["detail"].lower()


def test_sign_unknown_session_is_404():
    r = _sign("NOPE")
    assert r.status_code == 404


# ─── lock enforcement after signing ────────────────────────────────────


def test_post_sign_reading_is_409():
    session = _create_session()
    _end(session["id"])
    _sign(session["id"])

    db = SessionLocal()
    try:
        with pytest.raises(repo.SessionLocked):
            repo.save_reading(db, session["id"], {"hr": 80, "timestamp": 1})
    finally:
        db.close()


def test_post_sign_drug_is_409():
    session = _create_session()
    _end(session["id"])
    _sign(session["id"])

    r = client.post(
        f"/api/sessions/{session['id']}/drugs",
        json={"drugName": "Fentanyl", "dose": 100, "unit": "mcg", "route": "IV", "enteredBy": "Dr. X"},
    )
    assert r.status_code == 409


def test_post_sign_note_is_409():
    session = _create_session()
    _end(session["id"])
    _sign(session["id"])

    r = client.post(f"/api/sessions/{session['id']}/notes", json={"text": "late note", "category": "event"})
    assert r.status_code == 409


def test_post_sign_drug_correction_is_409():
    session = _create_session()
    drug = client.post(
        f"/api/sessions/{session['id']}/drugs",
        json={
            "drugName": "Propofol",
            "dose": 150,
            "unit": "mg",
            "route": "IV",
            "enteredBy": "Dr. Priya Sharma",
            "entryMethod": "quick_preset",
        },
    )
    assert drug.status_code == 201
    _end(session["id"])
    _sign(session["id"])

    r = client.patch(f"/api/drug-events/{drug.json()['id']}", json={"dose": 999})
    assert r.status_code == 409


def test_post_sign_flagged_confirm_is_409():
    session = _create_session()
    db = SessionLocal()
    flagged = repo.save_flagged(
        db,
        session["id"],
        {
            "timestamp": 1,
            "vital": "hr",
            "aiValue": "150",
            "suggestedValue": "90",
            "unit": "bpm",
            "confidence": 40.0,
            "severity": "critical",
            "frameNote": "n",
        },
    )
    db.close()
    _end(session["id"])
    _sign(session["id"])

    r = client.post(f"/api/flagged/{flagged.id}/confirm")
    assert r.status_code == 409


# ─── PDF ────────────────────────────────────────────────────────────────


def test_pdf_generated_contains_drug_log_and_combined_audit_history():
    session = _create_session()
    sid = session["id"]

    drug = client.post(
        f"/api/sessions/{sid}/drugs",
        json={
            "drugName": "Propofol",
            "dose": 150,
            "unit": "mg",
            "route": "IV",
            "enteredBy": "Dr. Priya Sharma",
            "entryMethod": "quick_preset",
        },
    )
    assert drug.status_code == 201

    db = SessionLocal()
    flagged = repo.save_flagged(
        db,
        sid,
        {
            "timestamp": 1,
            "vital": "hr",
            "aiValue": "150",
            "suggestedValue": "90",
            "unit": "bpm",
            "confidence": 40.0,
            "severity": "critical",
            "frameNote": "n",
        },
    )
    db.close()
    confirm = client.post(f"/api/flagged/{flagged.id}/confirm")
    assert confirm.status_code == 200

    _end(sid)
    signed = _sign(sid)
    assert signed.status_code == 200
    pdf_url = signed.json()["pdfUrl"]

    r = client.get(pdf_url)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 0

    on_disk = os.path.join("generated_reports", f"{sid}.pdf")
    assert os.path.exists(on_disk)
    assert os.path.getsize(on_disk) > 0

    text = _pdf_text(r.content)
    assert "Propofol" in text
    assert "Dr. Priya Sharma" in text
    assert "corrected HR" in text  # from the confirm's human_correct audit entry
    assert "audit-grade" in text.lower()


def test_report_pdf_before_signing_is_404():
    session = _create_session()
    r = client.get(f"/api/sessions/{session['id']}/report.pdf")
    assert r.status_code == 404
