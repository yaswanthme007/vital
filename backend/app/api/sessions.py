from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.db import repo
from app.db.session import SessionLocal
from app.models.base import CamelModel
from app.models.session import SessionFormData, SessionNoteCategory
from app.models.vitals import VitalType

router = APIRouter(prefix="/api")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NoteCreate(CamelModel):
    text: str
    category: SessionNoteCategory


class AlarmLimitUpdate(CamelModel):
    high_critical: Optional[float] = None
    high_warning: Optional[float] = None
    low_warning: Optional[float] = None
    low_critical: Optional[float] = None


def _require_session(db: OrmSession, session_id: str) -> None:
    if repo.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")


# ─── sessions ─────────────────────────────────────────────────────────────


@router.post("/sessions", status_code=201)
def create_session(form: SessionFormData, db: OrmSession = Depends(get_db)) -> dict:
    session = repo.create_session(db, form)
    return session.model_dump(by_alias=True)


@router.get("/sessions")
def list_sessions(status: Optional[str] = None, db: OrmSession = Depends(get_db)) -> List[dict]:
    if status == "completed":
        return [s.model_dump(by_alias=True) for s in repo.list_archived(db)]
    return [s.model_dump(by_alias=True) for s in repo.list_sessions(db, status=status)]


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: OrmSession = Depends(get_db)) -> dict:
    session = repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump(by_alias=True)


@router.post("/sessions/{session_id}/pause")
def pause_session(session_id: str, db: OrmSession = Depends(get_db)) -> dict:
    session = repo.update_session_status(db, session_id, "paused")
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump(by_alias=True)


@router.post("/sessions/{session_id}/resume")
def resume_session(session_id: str, db: OrmSession = Depends(get_db)) -> dict:
    session = repo.update_session_status(db, session_id, "active")
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump(by_alias=True)


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, db: OrmSession = Depends(get_db)) -> dict:
    archived = repo.end_session(db, session_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return archived.model_dump(by_alias=True)


@router.post("/sessions/{session_id}/notes", status_code=201)
def add_note(session_id: str, body: NoteCreate, db: OrmSession = Depends(get_db)) -> dict:
    note = repo.add_note(db, session_id, body.text, body.category)
    if note is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return note.model_dump(by_alias=True)


# ─── alarm limits ─────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/alarm-limits")
def get_alarm_limits(session_id: str, db: OrmSession = Depends(get_db)) -> List[dict]:
    _require_session(db, session_id)
    return [limit.model_dump(by_alias=True) for limit in repo.get_alarm_limits(db, session_id)]


@router.get("/sessions/{session_id}/alarm-limits/{vitalType}")
def get_alarm_limit(session_id: str, vitalType: VitalType, db: OrmSession = Depends(get_db)) -> dict:
    _require_session(db, session_id)
    limit = repo.get_alarm_limit(db, session_id, vitalType)
    if limit is None:
        raise HTTPException(status_code=404, detail="Alarm limit not found")
    return limit.model_dump(by_alias=True)


@router.put("/sessions/{session_id}/alarm-limits/{vitalType}")
def put_alarm_limit(
    session_id: str, vitalType: VitalType, body: AlarmLimitUpdate, db: OrmSession = Depends(get_db)
) -> dict:
    _require_session(db, session_id)
    limit = repo.upsert_alarm_limit(db, session_id, vitalType, body.model_dump())
    return limit.model_dump(by_alias=True)


# ─── alerts ───────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/alerts")
def get_alerts(session_id: str, active: Optional[bool] = None, db: OrmSession = Depends(get_db)) -> List[dict]:
    _require_session(db, session_id)
    return [alert.model_dump(by_alias=True) for alert in repo.list_alerts(db, session_id, active=active)]


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, db: OrmSession = Depends(get_db)) -> dict:
    alert = repo.ack_alert(db, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert.model_dump(by_alias=True)
