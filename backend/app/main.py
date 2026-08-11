from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import app.db.models  # noqa: F401 -- registers tables on Base.metadata before create_all
from app.api.chart import router as chart_router
from app.api.drugs import router as drugs_router
from app.api.flagged import router as flagged_router
from app.api.sessions import router as sessions_router
from app.db.repo import SessionLocked
from app.db.session import Base, engine
from app.ws.vitals import router as vitals_ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="VITAL Backend", lifespan=lifespan)


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
