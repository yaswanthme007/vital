"""Assembles a periodic anaesthesia chart from stored session data. Purely a
read-only aggregation over app.db.repo's existing readers (readings, drug
events, notes, alerts) — no new DB writes, and no frontend-facing Pydantic
model to match, so the returned dict shape is new/additive (same freedom as
S9's DRUG_PRESETS)."""

from typing import Dict, List, Optional

from sqlalchemy.orm import Session as OrmSession

from app.db import repo

VITAL_FIELDS = ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr")


def _generate_marks(start: int, end: int, interval_ms: int) -> List[int]:
    if end <= start:
        return [start]
    return list(range(start, end + 1, interval_ms))


def _field_index(readings: List[dict]) -> Dict[str, List[dict]]:
    """M5.7: per-field list of {timestamp, value}, one entry per reading
    where THAT field is non-null. Post-M5.7, app.ws.vitals.send_loop only
    persists fields reconcile() marked 'confirmed', so a row is sparse by
    design — a tick that confirmed only HR writes a row with every other
    field NULL. A single 'nearest row' (the pre-M5.7 approach) could
    therefore land on a row where HR is set but SpO2 happens to be null,
    even though a SpO2 confirmation exists one tick away. Indexing each
    field independently and nearest-matching it on its own means a mark's
    SpO2 always comes from the actual nearest SpO2 OBSERVATION, not from
    whichever row happened to be nearest overall."""
    index: Dict[str, List[dict]] = {field: [] for field in VITAL_FIELDS}
    for r in readings:
        for field in VITAL_FIELDS:
            value = r.get(field)
            if value is not None:
                index[field].append({"timestamp": r["timestamp"], "value": value})
    return index


def _nearest_field_value(entries: List[dict], mark: int) -> Optional[float]:
    if not entries:
        return None
    return min(entries, key=lambda e: abs(e["timestamp"] - mark))["value"]


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

    field_index = _field_index(readings)
    rows = []
    for mark in marks:
        row = {"timestamp": mark, "events": []}
        for field in VITAL_FIELDS:
            row[field] = _nearest_field_value(field_index[field], mark)
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

    # M5.7: same "prefer camera, fall back to everything" rule as
    # repo.end_session's summary — see that function's comment. Keeps the
    # PDF chart's own summary consistent with the session summary it's
    # printed alongside.
    camera_readings = [r for r in readings if r.get("source") == "camera"]
    summary_readings = camera_readings if camera_readings else readings
    hrs = [r["hr"] for r in summary_readings if r["hr"] is not None]
    spo2s = [r["spo2"] for r in summary_readings if r["spo2"] is not None]
    etco2s = [r["etco2"] for r in summary_readings if r["etco2"] is not None]

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
