import random
import time
from typing import List, Optional

from sqlalchemy.orm import Session as OrmSession

from app.db.models import (
    AlarmLimitRow,
    AlertRow,
    AuditEntryRow,
    DrugEventRow,
    FlaggedReadingRow,
    SessionNoteRow,
    SessionRow,
    VitalReadingRow,
)
from app.models.alert import Alert
from app.models.drug import DrugEvent
from app.models.review import AuditEntry, FlaggedReading
from app.models.session import ArchivedSession, Patient, Session, SessionFormData, SessionNote, VitalSummary
from app.models.vitals import AlarmLimit

# 5-minute correction window for drug_events, per task: dose/time can only be
# amended shortly after entry (an intraoperative record shouldn't be quietly
# rewritable long after the fact).
DRUG_CORRECTION_WINDOW_MS = 5 * 60 * 1000


class DrugCorrectionWindowExpired(Exception):
    """Raised by correct_drug_event() when recorded_at is more than
    DRUG_CORRECTION_WINDOW_MS in the past. Callers (app/api/drugs.py) map
    this to an HTTP 409."""

# Ported from frontend-reference/src/types/vitals.ts's DEFAULT_ALARM_LIMITS —
# seeded as 6 AlarmLimitRow per session at creation (alarm limits are
# per-session, not global — see routes nested under /sessions/{id}/alarm-limits).
DEFAULT_ALARM_LIMITS = [
    {"vital_type": "hr", "high_critical": 130, "high_warning": 110, "low_warning": 50, "low_critical": 40},
    {"vital_type": "spo2", "high_critical": None, "high_warning": 100, "low_warning": 94, "low_critical": 90},
    {"vital_type": "etco2", "high_critical": 60, "high_warning": 50, "low_warning": 25, "low_critical": 20},
    {"vital_type": "temp", "high_critical": 39.5, "high_warning": 38.5, "low_warning": 35.5, "low_critical": 34.5},
    {"vital_type": "rr", "high_critical": 30, "high_warning": 24, "low_warning": 8, "low_critical": 5},
    {"vital_type": "nibp", "high_critical": 180, "high_warning": 160, "low_warning": 90, "low_critical": 70},
]


def _gen_id(prefix: str) -> str:
    now_ms = int(time.time() * 1000)
    suffix = "".join(random.choices("0123456789abcdefghijklmnopqrstuvwxyz", k=4))
    return f"{prefix}-{now_ms}-{suffix}"


class SessionLocked(Exception):
    """Raised by assert_not_signed() for any mutation attempted on a session
    whose signed_at is already set. Callers (app/api/*.py) map this to an
    HTTP 409 via a single FastAPI exception handler in app.main, so no
    individual route re-implements the check."""


def assert_not_signed(db: OrmSession, session_id: str) -> None:
    """The single choke point every session-scoped mutator calls first. A
    signed session is immutable — once signed_at is set, nothing about it
    may change again, including re-signing. No-op if the session doesn't
    exist (the caller's own not-found handling takes priority over a lock
    check that can't apply to a session that isn't there)."""
    row = db.get(SessionRow, session_id)
    if row is not None and row.signed_at is not None:
        raise SessionLocked(f"Session {session_id} is signed and locked")


# ─── row <-> Pydantic model conversion ───────────────────────────────────────


def _notes_for_session(db: OrmSession, session_id: str) -> List[SessionNote]:
    rows = (
        db.query(SessionNoteRow)
        .filter(SessionNoteRow.session_id == session_id)
        .order_by(SessionNoteRow.timestamp)
        .all()
    )
    return [SessionNote(id=r.id, text=r.text, timestamp=r.timestamp, category=r.category) for r in rows]


def _session_fields(db: OrmSession, row: SessionRow) -> dict:
    return dict(
        id=row.id,
        patient=Patient(id=row.patient_id, age=row.patient_age, weight=row.patient_weight, asa=row.patient_asa),
        procedure=row.procedure,
        anesthetist=row.anesthetist,
        start_time=row.start_time,
        end_time=row.end_time,
        notes=_notes_for_session(db, row.id),
        status=row.status,
        signed_at=row.signed_at,
        archived_at=row.archived_at,
        interrupted_at=row.interrupted_at,
        current_owner=row.current_owner,
        signed_by=row.signed_by,
        signature_method=row.signature_method,
        pdf_url=row.pdf_url,
        vitals_count=row.vitals_count,
        drugs_count=row.drugs_count,
        events_count=row.events_count,
        flagged_count=row.flagged_count,
    )


def _session_row_to_model(db: OrmSession, row: SessionRow) -> Session:
    return Session(**_session_fields(db, row))


def _session_row_to_archived_model(db: OrmSession, row: SessionRow) -> ArchivedSession:
    return ArchivedSession(
        **_session_fields(db, row),
        vital_summary=VitalSummary(
            avg_hr=row.vital_summary_avg_hr or 0.0,
            min_spo2=row.vital_summary_min_spo2 or 0.0,
            avg_etco2=row.vital_summary_avg_etco2 or 0.0,
            duration_min=row.vital_summary_duration_min or 0.0,
        ),
    )


def _alert_row_to_model(row: AlertRow) -> Alert:
    return Alert(
        id=row.id,
        vital_type=row.vital_type,
        severity=row.severity,
        message=row.message,
        value=row.value,
        unit=row.unit,
        timestamp=row.timestamp,
        acknowledged=row.acknowledged,
    )


def _alarm_limit_row_to_model(row: AlarmLimitRow) -> AlarmLimit:
    return AlarmLimit(
        vital_type=row.vital_type,
        high_critical=row.high_critical,
        high_warning=row.high_warning,
        low_warning=row.low_warning,
        low_critical=row.low_critical,
    )


def _flagged_row_to_model(row: FlaggedReadingRow) -> FlaggedReading:
    return FlaggedReading(
        id=row.id,
        timestamp=row.timestamp,
        vital=row.vital,
        ai_value=row.ai_value,
        suggested_value=row.suggested_value,
        unit=row.unit,
        confidence=row.confidence,
        severity=row.severity,
        status=row.status,
        corrected_value=row.corrected_value,
        frame_note=row.frame_note,
    )


def _audit_row_to_model(row: AuditEntryRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        timestamp=row.timestamp,
        action=row.action,
        vital=row.vital,
        value=row.value,
        prev_value=row.prev_value,
        author=row.author,
        confidence=row.confidence,
    )


def _drug_event_row_to_model(row: DrugEventRow) -> DrugEvent:
    return DrugEvent(
        id=row.id,
        session_id=row.session_id,
        drug_name=row.drug_name,
        dose=row.dose,
        unit=row.unit,
        route=row.route,
        rate=row.rate,
        rate_unit=row.rate_unit,
        administered_at=row.administered_at,
        recorded_at=row.recorded_at,
        notes=row.notes,
        is_reversal=row.is_reversal,
        reversal_of=row.reversal_of,
        entry_method=row.entry_method,
        entered_by=row.entered_by,
        cumulative_dose=row.cumulative_dose,
    )


# ─── sessions ─────────────────────────────────────────────────────────────


def create_session(db: OrmSession, form: SessionFormData) -> Session:
    session_id = _gen_id("SESSION")
    row = SessionRow(
        id=session_id,
        patient_id=form.patient_id,
        patient_age=form.patient_age,
        patient_weight=form.patient_weight,
        patient_asa=form.asa,
        procedure=form.procedure,
        anesthetist=form.anesthetist,
        start_time=int(time.time() * 1000),
        status="active",
    )
    db.add(row)
    for limit in DEFAULT_ALARM_LIMITS:
        db.add(AlarmLimitRow(session_id=session_id, **limit))
    db.commit()
    db.refresh(row)
    return _session_row_to_model(db, row)


def get_session(db: OrmSession, session_id: str) -> Optional[Session]:
    row = db.get(SessionRow, session_id)
    return _session_row_to_model(db, row) if row else None


def update_session_status(db: OrmSession, session_id: str, status: str) -> Optional[Session]:
    row = db.get(SessionRow, session_id)
    if row is None:
        return None
    assert_not_signed(db, session_id)
    row.status = status
    db.commit()
    db.refresh(row)
    return _session_row_to_model(db, row)


def add_note(db: OrmSession, session_id: str, text: str, category: str) -> Optional[SessionNote]:
    if db.get(SessionRow, session_id) is None:
        return None
    assert_not_signed(db, session_id)
    note_row = SessionNoteRow(
        id=_gen_id("NOTE"),
        session_id=session_id,
        text=text,
        timestamp=int(time.time() * 1000),
        category=category,
    )
    db.add(note_row)
    db.commit()
    return SessionNote(id=note_row.id, text=note_row.text, timestamp=note_row.timestamp, category=note_row.category)


def list_notes(db: OrmSession, session_id: str) -> List[SessionNote]:
    return _notes_for_session(db, session_id)


def end_session(db: OrmSession, session_id: str) -> Optional[ArchivedSession]:
    row = db.get(SessionRow, session_id)
    if row is None:
        return None
    assert_not_signed(db, session_id)

    readings = db.query(VitalReadingRow).filter(VitalReadingRow.session_id == session_id).all()
    hrs = [r.hr for r in readings if r.hr is not None]
    spo2s = [r.spo2 for r in readings if r.spo2 is not None]
    etco2s = [r.etco2 for r in readings if r.etco2 is not None]

    row.end_time = int(time.time() * 1000)
    row.status = "completed"
    row.vital_summary_avg_hr = (sum(hrs) / len(hrs)) if hrs else 0.0
    row.vital_summary_min_spo2 = min(spo2s) if spo2s else 0.0
    row.vital_summary_avg_etco2 = (sum(etco2s) / len(etco2s)) if etco2s else 0.0
    row.vital_summary_duration_min = (row.end_time - row.start_time) / 60000.0

    db.commit()
    db.refresh(row)
    return _session_row_to_archived_model(db, row)


def list_archived(db: OrmSession) -> List[ArchivedSession]:
    rows = (
        db.query(SessionRow)
        .filter(SessionRow.status == "completed")
        .order_by(SessionRow.end_time.desc())
        .all()
    )
    return [_session_row_to_archived_model(db, row) for row in rows]


def list_sessions(db: OrmSession, status: Optional[str] = None) -> List[Session]:
    """Plain (non-archived-shape) session listing, optionally filtered by
    status — the fallback for GET /api/sessions when status != 'completed'."""
    query = db.query(SessionRow)
    if status is not None:
        query = query.filter(SessionRow.status == status)
    rows = query.order_by(SessionRow.start_time.desc()).all()
    return [_session_row_to_model(db, row) for row in rows]


# ─── readings ─────────────────────────────────────────────────────────────


def save_reading(
    db: OrmSession,
    session_id: str,
    reading: dict,
    confidence: Optional[float] = None,
    provenance: Optional[str] = None,
    per_vital_confidence: Optional[dict] = None,
) -> None:
    assert_not_signed(db, session_id)
    row = VitalReadingRow(
        session_id=session_id,
        hr=reading.get("hr"),
        spo2=reading.get("spo2"),
        nibp_systolic=reading.get("nibpSystolic"),
        nibp_diastolic=reading.get("nibpDiastolic"),
        nibp_mean=reading.get("nibpMean"),
        etco2=reading.get("etco2"),
        temp=reading.get("temp"),
        rr=reading.get("rr"),
        timestamp=reading.get("timestamp") or int(time.time() * 1000),
        confidence=confidence,
        provenance=provenance,
        per_vital_confidence=per_vital_confidence,
    )
    db.add(row)

    session_row = db.get(SessionRow, session_id)
    if session_row is not None:
        session_row.vitals_count = (session_row.vitals_count or 0) + 1

    db.commit()


def list_readings(db: OrmSession, session_id: str) -> List[dict]:
    """Returns plain camelCase reading dicts, NOT the strict VitalReading
    Pydantic model — OCR-sourced readings can have per-vital gaps (None),
    but VitalReading requires all 8 numeric fields to be non-null floats
    (matching the frontend exactly). No route in this session returns
    readings directly; this is consumed internally (end_session's summary)
    and by tests asserting persistence/ordering."""
    rows = (
        db.query(VitalReadingRow)
        .filter(VitalReadingRow.session_id == session_id)
        .order_by(VitalReadingRow.timestamp)
        .all()
    )
    return [
        {
            "hr": r.hr,
            "spo2": r.spo2,
            "nibpSystolic": r.nibp_systolic,
            "nibpDiastolic": r.nibp_diastolic,
            "nibpMean": r.nibp_mean,
            "etco2": r.etco2,
            "temp": r.temp,
            "rr": r.rr,
            "timestamp": r.timestamp,
            "confidence": r.confidence,
            "provenance": r.provenance,
            "perVitalConfidence": r.per_vital_confidence,
        }
        for r in rows
    ]


# ─── alerts ───────────────────────────────────────────────────────────────


def save_alert(db: OrmSession, session_id: str, alert: dict) -> Alert:
    assert_not_signed(db, session_id)
    row = AlertRow(
        id=alert["id"],
        session_id=session_id,
        vital_type=alert["vitalType"],
        severity=alert["severity"],
        message=alert["message"],
        value=alert.get("value"),
        unit=alert.get("unit"),
        timestamp=alert["timestamp"],
        acknowledged=alert.get("acknowledged", False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _alert_row_to_model(row)


def list_alerts(db: OrmSession, session_id: str, active: Optional[bool] = None) -> List[Alert]:
    query = db.query(AlertRow).filter(AlertRow.session_id == session_id)
    if active is True:
        query = query.filter(AlertRow.acknowledged.is_(False))
    elif active is False:
        query = query.filter(AlertRow.acknowledged.is_(True))
    rows = query.order_by(AlertRow.timestamp.desc()).all()
    return [_alert_row_to_model(row) for row in rows]


def ack_alert(db: OrmSession, alert_id: str) -> Optional[Alert]:
    row = db.get(AlertRow, alert_id)
    if row is None:
        return None
    assert_not_signed(db, row.session_id)
    row.acknowledged = True
    db.commit()
    db.refresh(row)
    return _alert_row_to_model(row)


# ─── alarm limits ─────────────────────────────────────────────────────────


def get_alarm_limits(db: OrmSession, session_id: str) -> List[AlarmLimit]:
    rows = db.query(AlarmLimitRow).filter(AlarmLimitRow.session_id == session_id).all()
    return [_alarm_limit_row_to_model(row) for row in rows]


def get_alarm_limit(db: OrmSession, session_id: str, vital_type: str) -> Optional[AlarmLimit]:
    row = (
        db.query(AlarmLimitRow)
        .filter(AlarmLimitRow.session_id == session_id, AlarmLimitRow.vital_type == vital_type)
        .first()
    )
    return _alarm_limit_row_to_model(row) if row else None


def upsert_alarm_limit(db: OrmSession, session_id: str, vital_type: str, limit_data: dict) -> AlarmLimit:
    assert_not_signed(db, session_id)
    row = (
        db.query(AlarmLimitRow)
        .filter(AlarmLimitRow.session_id == session_id, AlarmLimitRow.vital_type == vital_type)
        .first()
    )
    if row is None:
        row = AlarmLimitRow(session_id=session_id, vital_type=vital_type)
        db.add(row)

    row.high_critical = limit_data.get("high_critical")
    row.high_warning = limit_data.get("high_warning")
    row.low_warning = limit_data.get("low_warning")
    row.low_critical = limit_data.get("low_critical")

    db.commit()
    db.refresh(row)
    return _alarm_limit_row_to_model(row)


# ─── flagged readings ─────────────────────────────────────────────────────


def save_flagged(db: OrmSession, session_id: str, flagged_data: dict) -> FlaggedReading:
    assert_not_signed(db, session_id)
    row = FlaggedReadingRow(
        id=_gen_id("FLAG"),
        session_id=session_id,
        timestamp=flagged_data["timestamp"],
        vital=flagged_data["vital"],
        ai_value=flagged_data["aiValue"],
        suggested_value=flagged_data["suggestedValue"],
        unit=flagged_data["unit"],
        confidence=flagged_data["confidence"],
        severity=flagged_data["severity"],
        status=flagged_data.get("status", "pending"),
        corrected_value=flagged_data.get("correctedValue"),
        frame_note=flagged_data["frameNote"],
    )
    db.add(row)

    session_row = db.get(SessionRow, session_id)
    if session_row is not None:
        session_row.flagged_count = (session_row.flagged_count or 0) + 1

    db.commit()
    db.refresh(row)
    return _flagged_row_to_model(row)


def list_flagged(db: OrmSession, session_id: str, status: Optional[str] = None) -> List[FlaggedReading]:
    query = db.query(FlaggedReadingRow).filter(FlaggedReadingRow.session_id == session_id)
    if status is not None:
        query = query.filter(FlaggedReadingRow.status == status)
    rows = query.order_by(FlaggedReadingRow.timestamp.desc()).all()
    return [_flagged_row_to_model(row) for row in rows]


def get_flagged(db: OrmSession, flagged_id: str) -> Optional[FlaggedReading]:
    row = db.get(FlaggedReadingRow, flagged_id)
    return _flagged_row_to_model(row) if row else None


def get_flagged_session_id(db: OrmSession, flagged_id: str) -> Optional[str]:
    row = db.get(FlaggedReadingRow, flagged_id)
    return row.session_id if row else None


def update_flagged_status(
    db: OrmSession, flagged_id: str, status: str, corrected_value: Optional[str] = None
) -> Optional[FlaggedReading]:
    row = db.get(FlaggedReadingRow, flagged_id)
    if row is None:
        return None
    assert_not_signed(db, row.session_id)
    row.status = status
    row.corrected_value = corrected_value
    db.commit()
    db.refresh(row)
    return _flagged_row_to_model(row)


# ─── audit entries (append-only — no update/delete function exists) ───────


def save_audit_entry(
    db: OrmSession,
    session_id: str,
    action: str,
    vital: str,
    value: str,
    author: str,
    prev_value: Optional[str] = None,
    confidence: Optional[float] = None,
    flagged_id: Optional[str] = None,
) -> AuditEntry:
    row = AuditEntryRow(
        id=_gen_id("AUDIT"),
        session_id=session_id,
        flagged_id=flagged_id,
        timestamp=int(time.time() * 1000),
        action=action,
        vital=vital,
        value=value,
        prev_value=prev_value,
        author=author,
        confidence=confidence,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _audit_row_to_model(row)


def list_audit(db: OrmSession, session_id: str) -> List[AuditEntry]:
    rows = (
        db.query(AuditEntryRow)
        .filter(AuditEntryRow.session_id == session_id)
        .order_by(AuditEntryRow.timestamp)
        .all()
    )
    return [_audit_row_to_model(row) for row in rows]


# ─── drug events ────────────────────────────────────────────────────────


def _recompute_cumulative_doses(db: OrmSession, session_id: str, drug_name: str) -> None:
    """Running total in administered_at order — recomputed (not just
    incremented) so a later correction to a dose or to administeredAt
    (which can reorder events) keeps every event's cumulativeDose for that
    drug in that session consistent, not just the newest one."""
    rows = (
        db.query(DrugEventRow)
        .filter(DrugEventRow.session_id == session_id, DrugEventRow.drug_name == drug_name)
        .order_by(DrugEventRow.administered_at)
        .all()
    )
    running_total = 0.0
    for row in rows:
        running_total += row.dose
        row.cumulative_dose = running_total


def save_drug_event(db: OrmSession, session_id: str, drug_data: dict) -> DrugEvent:
    assert_not_signed(db, session_id)
    row = DrugEventRow(
        id=_gen_id("DRUG"),
        session_id=session_id,
        drug_name=drug_data["drugName"],
        dose=drug_data["dose"],
        unit=drug_data["unit"],
        route=drug_data["route"],
        rate=drug_data.get("rate"),
        rate_unit=drug_data.get("rateUnit"),
        administered_at=drug_data.get("administeredAt") or int(time.time() * 1000),
        recorded_at=int(time.time() * 1000),
        notes=drug_data.get("notes"),
        is_reversal=drug_data.get("isReversal", False),
        reversal_of=drug_data.get("reversalOf"),
        entry_method=drug_data.get("entryMethod", "manual"),
        entered_by=drug_data["enteredBy"],
    )
    db.add(row)
    db.flush()  # assign the row before recomputing the running total over it

    _recompute_cumulative_doses(db, session_id, row.drug_name)

    session_row = db.get(SessionRow, session_id)
    if session_row is not None:
        session_row.drugs_count = (session_row.drugs_count or 0) + 1

    db.commit()
    db.refresh(row)
    return _drug_event_row_to_model(row)


def list_drug_events(db: OrmSession, session_id: str) -> List[DrugEvent]:
    rows = (
        db.query(DrugEventRow)
        .filter(DrugEventRow.session_id == session_id)
        .order_by(DrugEventRow.administered_at)
        .all()
    )
    return [_drug_event_row_to_model(row) for row in rows]


def get_drug_event(db: OrmSession, drug_event_id: str) -> Optional[DrugEvent]:
    row = db.get(DrugEventRow, drug_event_id)
    return _drug_event_row_to_model(row) if row else None


def correct_drug_event(db: OrmSession, drug_event_id: str, updates: dict, author: str) -> Optional[DrugEvent]:
    """updates: a dict that may contain "dose" and/or "administeredAt".
    Raises DrugCorrectionWindowExpired if more than 5 minutes have passed
    since recorded_at — an intraoperative record shouldn't be quietly
    rewritable long after the fact. Every applied change is appended to the
    row's correction_history (see DrugEventRow for why this isn't the
    shared AuditEntry model)."""
    row = db.get(DrugEventRow, drug_event_id)
    if row is None:
        return None
    assert_not_signed(db, row.session_id)

    now_ms = int(time.time() * 1000)
    if now_ms - row.recorded_at > DRUG_CORRECTION_WINDOW_MS:
        raise DrugCorrectionWindowExpired(f"Correction window expired for drug event {drug_event_id}")

    history = list(row.correction_history or [])
    dose_or_time_changed = False

    if "dose" in updates and updates["dose"] != row.dose:
        history.append({"timestamp": now_ms, "field": "dose", "prevValue": row.dose, "newValue": updates["dose"], "author": author})
        row.dose = updates["dose"]
        dose_or_time_changed = True

    if "administeredAt" in updates and updates["administeredAt"] != row.administered_at:
        history.append(
            {
                "timestamp": now_ms,
                "field": "administeredAt",
                "prevValue": row.administered_at,
                "newValue": updates["administeredAt"],
                "author": author,
            }
        )
        row.administered_at = updates["administeredAt"]
        dose_or_time_changed = True

    row.correction_history = history
    db.flush()

    if dose_or_time_changed:
        _recompute_cumulative_doses(db, row.session_id, row.drug_name)

    db.commit()
    db.refresh(row)
    return _drug_event_row_to_model(row)


def list_drug_corrections(db: OrmSession, drug_event_id: str) -> List[dict]:
    row = db.get(DrugEventRow, drug_event_id)
    return list(row.correction_history or []) if row else []


def list_recent_drugs_by_user(db: OrmSession, user: str, limit: int = 10) -> List[str]:
    """Last `limit` DISTINCT drug names entered by `user`, most-recent use
    first — a quick-select shortcut, separate from the static presets list."""
    rows = (
        db.query(DrugEventRow)
        .filter(DrugEventRow.entered_by == user)
        .order_by(DrugEventRow.administered_at.desc())
        .all()
    )
    distinct_names: List[str] = []
    for row in rows:
        if row.drug_name not in distinct_names:
            distinct_names.append(row.drug_name)
        if len(distinct_names) >= limit:
            break
    return distinct_names


# ─── sign / lock ────────────────────────────────────────────────────────


class SessionNotCompleted(Exception):
    """Raised by sign_session() when the session's status isn't 'completed'
    yet. Callers (app/api/chart.py) map this to an HTTP 409, distinct from
    the SessionLocked 409 raised for an already-signed session."""


def sign_session(db: OrmSession, session_id: str, author: str, signature_method: str) -> Optional[Session]:
    """Sets signed_at/signed_by/signature_method — the durable record of the
    signature (see DrugEventRow's correction_history precedent: AuditEntry's
    action/vital Literals can't represent a signature event without
    changing that frozen shape, so it isn't forced through save_audit_entry;
    these session fields ARE the signature record, read by both the API
    response and the PDF's signature block).

    status is deliberately left at "completed" — the frontend's SessionStatus
    type has no "locked" value; "locked" is derived as
    status == "completed" and signed_at is not None, not a status transition.
    """
    row = db.get(SessionRow, session_id)
    if row is None:
        return None
    if row.status != "completed":
        raise SessionNotCompleted(f"Session {session_id} must be completed before it can be signed")
    assert_not_signed(db, session_id)  # raises SessionLocked if already signed — same mechanism as re-signing

    row.signed_at = int(time.time() * 1000)
    row.signed_by = author
    row.signature_method = signature_method
    db.commit()
    db.refresh(row)
    return _session_row_to_model(db, row)


def set_pdf_url(db: OrmSession, session_id: str, pdf_url: str) -> Optional[Session]:
    """Separate from sign_session() because the PDF is generated (and needs
    signedAt/signedBy/signatureMethod already present to render its
    signature block) after the signature itself is committed."""
    row = db.get(SessionRow, session_id)
    if row is None:
        return None
    row.pdf_url = pdf_url
    db.commit()
    db.refresh(row)
    return _session_row_to_model(db, row)
