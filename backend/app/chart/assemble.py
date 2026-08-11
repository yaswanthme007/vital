"""Assembles a periodic anaesthesia chart from stored session data. Purely a
read-only aggregation over app.db.repo's existing readers (readings, drug
events, notes, alerts) — no new DB writes, and no frontend-facing Pydantic
model to match, so the returned dict shape is new/additive (same freedom as
S9's DRUG_PRESETS)."""

from typing import List, Optional

from sqlalchemy.orm import Session as OrmSession

from app.db import repo

VITAL_FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr")


def _generate_marks(start: int, end: int, interval_ms: int) -> List[int]:
    if end <= start:
        return [start]
    return list(range(start, end + 1, interval_ms))


def _nearest_reading(readings: List[dict], mark: int) -> dict:
    if not readings:
        return {}
    return min(readings, key=lambda r: abs(r["timestamp"] - mark))


def _nearest_mark_index(marks: List[int], timestamp: int) -> int:
    return min(range(len(marks)), key=lambda i: abs(marks[i] - timestamp))


def build_chart(db: OrmSession, session_id: str, interval_minutes: float = 5) -> Optional[dict]:
    session = repo.get_session(db, session_id)
    if session is None:
        return None

    readings = repo.list_readings(db, session_id)
    drug_events = repo.list_drug_events(db, session_id)
    notes = repo.list_notes(db, session_id)
    alerts = repo.list_alerts(db, session_id)

    start = int(session.start_time)
    if session.end_time is not None:
        end = int(session.end_time)
    else:
        candidate_timestamps = (
            [r["timestamp"] for r in readings]
            + [d.administered_at for d in drug_events]
            + [n.timestamp for n in notes]
            + [a.timestamp for a in alerts]
        )
        end = int(max(candidate_timestamps)) if candidate_timestamps else start

    interval_ms = int(interval_minutes * 60 * 1000)
    marks = _generate_marks(start, end, interval_ms)

    rows = []
    for mark in marks:
        nearest = _nearest_reading(readings, mark)
        row = {"timestamp": mark, "events": []}
        for field in VITAL_FIELDS:
            row[field] = nearest.get(field)
        rows.append(row)

    def _overlay(timestamp: int, event: dict) -> None:
        rows[_nearest_mark_index(marks, timestamp)]["events"].append(event)

    for d in drug_events:
        _overlay(
            d.administered_at,
            {
                "type": "drug",
                "timestamp": d.administered_at,
                "drugName": d.drug_name,
                "dose": d.dose,
                "unit": d.unit,
                "route": d.route,
                "cumulativeDose": d.cumulative_dose,
            },
        )

    for n in notes:
        _overlay(n.timestamp, {"type": "note", "timestamp": n.timestamp, "text": n.text, "category": n.category})

    for a in alerts:
        _overlay(
            a.timestamp,
            {
                "type": "alert",
                "timestamp": a.timestamp,
                "vitalType": a.vital_type,
                "severity": a.severity,
                "message": a.message,
            },
        )

    for row in rows:
        row["events"].sort(key=lambda e: e["timestamp"])

    hrs = [r["hr"] for r in readings if r["hr"] is not None]
    spo2s = [r["spo2"] for r in readings if r["spo2"] is not None]
    etco2s = [r["etco2"] for r in readings if r["etco2"] is not None]

    vital_summary = {
        "avgHr": (sum(hrs) / len(hrs)) if hrs else 0.0,
        "minSpo2": min(spo2s) if spo2s else 0.0,
        "avgEtco2": (sum(etco2s) / len(etco2s)) if etco2s else 0.0,
        "durationMin": (end - start) / 60000.0 if end > start else 0.0,
    }

    return {
        "session": {
            "patientId": session.patient.id,
            "procedure": session.procedure,
            "anesthetist": session.anesthetist,
            "startTime": session.start_time,
            "endTime": session.end_time,
            "status": session.status,
            "signedAt": session.signed_at,
            "signedBy": session.signed_by,
            "signatureMethod": session.signature_method,
        },
        "intervalMinutes": interval_minutes,
        "rows": rows,
        "vitalSummary": vital_summary,
    }
