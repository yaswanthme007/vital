from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import app.db.models  # noqa: F401 -- registers tables on Base.metadata before create_all
from app.api.chart import router as chart_router
from app.api.drugs import router as drugs_router
from app.api.flagged import router as flagged_router
from app.api.pipeline import router as pipeline_router
from app.api.sessions import router as sessions_router
from app.db.repo import SessionLocked
from app.db.session import Base, engine
from app.ws.vitals import router as vitals_ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
