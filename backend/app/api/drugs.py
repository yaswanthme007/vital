import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as OrmSession

from app.db import repo
from app.db.session import SessionLocal
from app.drugs.presets import DRUG_PRESETS
from app.models.base import CamelModel
from app.models.drug import DrugEntryMethod

router = APIRouter(prefix="/api")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DrugEventCreate(CamelModel):
    drug_name: str
    dose: float
    unit: str
    route: str
    rate: Optional[float] = None
    rate_unit: Optional[str] = None
    administered_at: Optional[float] = None  # defaults to now if omitted
    notes: Optional[str] = None
    is_reversal: bool = False
    reversal_of: Optional[str] = None
    entry_method: DrugEntryMethod = "manual"
    entered_by: str


class DrugEventUpdate(CamelModel):
    """PATCH body: correct dose and/or administeredAt within the 5-min
    window. `author` documents who made the correction."""

    dose: Optional[float] = None
    administered_at: Optional[float] = None
    author: Optional[str] = None


@router.post("/sessions/{session_id}/drugs", status_code=201)
def log_drug(session_id: str, body: DrugEventCreate, db: OrmSession = Depends(get_db)) -> dict:
    if repo.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    drug_data = body.model_dump(by_alias=True)
    if drug_data.get("administeredAt") is None:
        drug_data["administeredAt"] = int(time.time() * 1000)

    event = repo.save_drug_event(db, session_id, drug_data)
    return event.model_dump(by_alias=True)


@router.get("/sessions/{session_id}/drugs")
def get_drugs(session_id: str, db: OrmSession = Depends(get_db)) -> List[dict]:
    if repo.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return [e.model_dump(by_alias=True) for e in repo.list_drug_events(db, session_id)]


@router.patch("/drug-events/{drug_event_id}")
def correct_drug_event(drug_event_id: str, body: DrugEventUpdate, db: OrmSession = Depends(get_db)) -> dict:
    updates = {}
    if body.dose is not None:
        updates["dose"] = body.dose
    if body.administered_at is not None:
        updates["administeredAt"] = body.administered_at
    author = body.author or "Anaesthetist"

    try:
        event = repo.correct_drug_event(db, drug_event_id, updates, author=author)
    except repo.DrugCorrectionWindowExpired:
        raise HTTPException(status_code=409, detail="Correction window (5 minutes since recordedAt) has expired")

    if event is None:
        raise HTTPException(status_code=404, detail="Drug event not found")
    return event.model_dump(by_alias=True)


@router.get("/drugs/presets")
def get_presets() -> list:
    return DRUG_PRESETS


@router.get("/drugs/recent")
def get_recent_drugs(user: str = Query(...), db: OrmSession = Depends(get_db)) -> List[str]:
    return repo.list_recent_drugs_by_user(db, user, limit=10)
