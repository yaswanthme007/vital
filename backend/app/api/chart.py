import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session as OrmSession

from app.chart.assemble import build_chart
from app.chart.pdf import generate_pdf
from app.db import repo
from app.db.session import SessionLocal
from app.models.base import CamelModel

router = APIRouter(prefix="/api")

# Gitignored, same pattern as simulator/out/ — generated artifacts, not source.
REPORTS_DIR = os.environ.get("REPORTS_DIR", "generated_reports")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SignRequest(CamelModel):
    author: str
    signature_method: str


def _pdf_path(session_id: str) -> str:
    return os.path.join(REPORTS_DIR, f"{session_id}.pdf")


@router.get("/sessions/{session_id}/chart")
def get_chart(session_id: str, interval_minutes: float = 5, db: OrmSession = Depends(get_db)) -> dict:
    chart = build_chart(db, session_id, interval_minutes=interval_minutes)
    if chart is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return chart


@router.post("/sessions/{session_id}/sign")
def sign_session(session_id: str, body: SignRequest, db: OrmSession = Depends(get_db)) -> dict:
    try:
        signed = repo.sign_session(db, session_id, author=body.author, signature_method=body.signature_method)
    except repo.SessionNotCompleted:
        raise HTTPException(status_code=409, detail="Session must be completed before it can be signed")

    if signed is None:
        raise HTTPException(status_code=404, detail="Session not found")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    pdf_bytes = generate_pdf(db, session_id)
    with open(_pdf_path(session_id), "wb") as f:
        f.write(pdf_bytes)

    signed = repo.set_pdf_url(db, session_id, f"/api/sessions/{session_id}/report.pdf")
    return signed.model_dump(by_alias=True)


@router.get("/sessions/{session_id}/report.pdf")
def get_report_pdf(session_id: str, db: OrmSession = Depends(get_db)) -> Response:
    session = repo.get_session(db, session_id)
    if session is None or session.pdf_url is None:
        raise HTTPException(status_code=404, detail="Report not generated yet")

    try:
        with open(_pdf_path(session_id), "rb") as f:
            data = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Report file missing on disk")

    return Response(content=data, media_type="application/pdf")
