from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.db import repo
from app.db.session import SessionLocal
from app.models.base import CamelModel

router = APIRouter(prefix="/api")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ActionBody(CamelModel):
    """confirm/dismiss take no data beyond an optional author override —
    this may arrive as an empty/absent request body."""

    author: Optional[str] = None


class CorrectBody(CamelModel):
    corrected_value: str
    author: Optional[str] = None


def _resolve_author(db: OrmSession, session_id: str, author: Optional[str]) -> str:
    """Matches the frontend's own fallback exactly:
    `session?.anesthetist ?? 'Anaesthetist'` (ReviewPage.tsx handleCorrect/handleDismiss)."""
    if author:
        return author
    session = repo.get_session(db, session_id)
    return session.anesthetist if session else "Anaesthetist"


def _get_flagged_or_404(db: OrmSession, flagged_id: str):
    flagged = repo.get_flagged(db, flagged_id)
    if flagged is None:
        raise HTTPException(status_code=404, detail="Flagged reading not found")
    session_id = repo.get_flagged_session_id(db, flagged_id)
    return flagged, session_id


@router.post("/flagged/{flagged_id}/confirm")
def confirm_flagged(flagged_id: str, body: Optional[ActionBody] = None, db: OrmSession = Depends(get_db)) -> dict:
    """'Confirm' = adopt the system's own suggested value as correct — a
    one-click shortcut for the common case where the reviewer agrees with
    what's already suggested. Recorded as human_correct (there's no
    human_confirm action in the AuditEntry shape), value=suggestedValue."""
    flagged, session_id = _get_flagged_or_404(db, flagged_id)
    author = _resolve_author(db, session_id, body.author if body else None)

    updated = repo.update_flagged_status(db, flagged_id, "corrected", corrected_value=flagged.suggested_value)
    repo.save_audit_entry(
        db,
        session_id,
        action="human_correct",
        vital=flagged.vital,
        value=flagged.suggested_value,
        prev_value=flagged.ai_value,
        author=author,
        confidence=flagged.confidence,
        flagged_id=flagged_id,
    )
    return updated.model_dump(by_alias=True)


@router.post("/flagged/{flagged_id}/correct")
def correct_flagged(flagged_id: str, body: CorrectBody, db: OrmSession = Depends(get_db)) -> dict:
    """'Correct' = the reviewer supplies a specific value (may differ from
    the system's own suggestion)."""
    flagged, session_id = _get_flagged_or_404(db, flagged_id)
    author = _resolve_author(db, session_id, body.author)

    updated = repo.update_flagged_status(db, flagged_id, "corrected", corrected_value=body.corrected_value)
    repo.save_audit_entry(
        db,
        session_id,
        action="human_correct",
        vital=flagged.vital,
        value=body.corrected_value,
        prev_value=flagged.ai_value,
        author=author,
        confidence=flagged.confidence,
        flagged_id=flagged_id,
    )
    return updated.model_dump(by_alias=True)


@router.post("/flagged/{flagged_id}/dismiss")
def dismiss_flagged(flagged_id: str, body: Optional[ActionBody] = None, db: OrmSession = Depends(get_db)) -> dict:
    """'Dismiss' = leave the AI value exactly as read — reviewed and judged
    not worth correcting. Matches the frontend's handleDismiss exactly:
    value=item.aiValue, no prevValue."""
    flagged, session_id = _get_flagged_or_404(db, flagged_id)
    author = _resolve_author(db, session_id, body.author if body else None)

    updated = repo.update_flagged_status(db, flagged_id, "dismissed")
    repo.save_audit_entry(
        db,
        session_id,
        action="human_dismiss",
        vital=flagged.vital,
        value=flagged.ai_value,
        author=author,
        confidence=flagged.confidence,
        flagged_id=flagged_id,
    )
    return updated.model_dump(by_alias=True)


@router.get("/sessions/{session_id}/flagged")
def list_flagged(session_id: str, status: Optional[str] = None, db: OrmSession = Depends(get_db)) -> List[dict]:
    if repo.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return [f.model_dump(by_alias=True) for f in repo.list_flagged(db, session_id, status=status)]


@router.get("/sessions/{session_id}/audit")
def list_audit(session_id: str, db: OrmSession = Depends(get_db)) -> List[dict]:
    if repo.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return [a.model_dump(by_alias=True) for a in repo.list_audit(db, session_id)]
