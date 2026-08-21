import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

import app.db.models  # noqa: F401 -- registers tables on Base.metadata before create_all
from app.api.calibration import router as calibration_router
from app.api.chart import router as chart_router
from app.api.drugs import router as drugs_router
from app.api.flagged import router as flagged_router
from app.api.pipeline import router as pipeline_router
from app.api.sessions import router as sessions_router
from app.db.repo import SessionLocked
from app.db.session import Base, engine
from app.ws.vitals import router as vitals_ws_router


def _add_missing_columns() -> None:
    """Base.metadata.create_all() (below) adds missing TABLES to an existing
    database but never missing COLUMNS on a table that already exists --
    documented at app.db.models.CalibrationReferenceFrameRow's own
    docstring, which is why M5.3 chose a new table over new columns. M5.7's
    VitalReadingRow.source/field_status genuinely belong on the existing
    row (see that model's docstring), so this is the smallest thing that
    makes a pre-M5.7 vital.db upgrade cleanly instead of 500ing the first
    time a query touches a column that isn't there. Every column added here
    is nullable, so a fresh (post-M5.7) database created straight from
    create_all() already has them and this is a same-columns no-op for it."""
    inspector = inspect(engine)
    if "vital_readings" not in inspector.get_table_names():
        return  # create_all (below) will create the table with every column already present
    existing = {col["name"] for col in inspector.get_columns("vital_readings")}
    missing = {
        "source": "VARCHAR",
        "field_status": "JSON",
    }
    with engine.begin() as conn:
        for name, sql_type in missing.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE vital_readings ADD COLUMN {name} {sql_type}"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _add_missing_columns()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="VITAL Backend", lifespan=lifespan)

# The frontend (Vite dev server) runs on a different origin/port than this
# API, so browser fetches need explicit CORS headers — curl/server-to-server
# calls don't enforce this, which is why it's easy to miss in backend-only
# testing.
#
# allow_origin_regex additionally covers the dev server reached from another
# device on the same private LAN (e.g. a phone, for testing the camera
# pipeline against VITAL's own screen instead of localhost) — RFC 1918
# private ranges only, still port :5173 only, not a public wildcard.
#
# VITAL_EXTRA_CORS_ORIGINS (comma-separated) appends additional exact
# origins. It exists so an end-to-end run can serve the frontend on a
# THROWAWAY vite port against a throwaway backend, without disturbing an
# instance already serving the ordinary 5173/8000 pair -- see
# scripts/m5_7_1_flow_e2e.mjs's VITAL_E2E_APP/VITAL_E2E_API. Unset (every
# normal run) the allow-list below is byte-identical to what it always was;
# it never becomes a wildcard.
_extra_cors_origins = [o.strip() for o in os.environ.get("VITAL_EXTRA_CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *_extra_cors_origins],
    allow_origin_regex=r"http://(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):5173",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SessionLocked)
async def session_locked_handler(request: Request, exc: SessionLocked) -> JSONResponse:
    """Single choke point for the sign/lock invariant: every repo mutator
    raises SessionLocked via assert_not_signed() instead of each route
    re-implementing the check, so this handler is what turns that into the
    409 every caller sees."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(vitals_ws_router)
app.include_router(sessions_router)
app.include_router(flagged_router)
app.include_router(drugs_router)
app.include_router(chart_router)
app.include_router(pipeline_router)
app.include_router(calibration_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config/snapshot")
def config_snapshot() -> dict:
    """M5.6: ask the RUNNING process what configuration it is actually in,
    rather than trusting a document that was transcribed by hand. Read-only
    — see app.config_snapshot for why this exists and what it deliberately
    does not do. Also the mechanism behind docs/M5_6_FROZEN_CONFIG.json."""
    from app.config_snapshot import snapshot

    return snapshot()
