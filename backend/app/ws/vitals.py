import asyncio
import logging
import random
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.alerts.rules import AlertThrottle, build_alert, check_alerts
from app.db import repo
from app.db.session import SessionLocal
from app.sources.base import VitalsSource
from app.sources.replay import ReplaySource
from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile

# Matches frontend-reference/src/store/vitalsStore.ts's MAX_HISTORY exactly —
# the WS connection keeps at most this many past readings in memory per
# session rather than an unbounded list.
MAX_HISTORY = 360

logger = logging.getLogger(__name__)

router = APIRouter()


def _mean_confidence(per_vital_confidence: Optional[dict]) -> Optional[float]:
    if not per_vital_confidence:
        return None
    values = list(per_vital_confidence.values())
    return sum(values) / len(values) if values else None


def _ephemeral_id(prefix: str) -> str:
    """Same PREFIX-<epoch_ms>-<4char> shape as app.db.repo._gen_id / S6's
    build_alert, used for display-only envelopes that aren't being persisted
    (e.g. a 'flagged' envelope on a session that doesn't exist / isn't
    active) — never written to the DB, so it doesn't need repo's generator."""
    suffix = "".join(random.choices("0123456789abcdefghijklmnopqrstuvwxyz", k=4))
    return f"{prefix}-{int(time.time() * 1000)}-{suffix}"


def _session_is_active(session_factory: Callable, session_id: str) -> bool:
    """Closes the S7 orphan-readings gap: persistence only happens for a
    session that exists AND is currently 'active' — re-checked fresh every
    tick (not just once at connection start), so pausing/ending a session
    via REST stops new writes immediately without tearing down the WS
    connection itself. The stream keeps running either way (per-tick skip,
    not a connection reject) so demo/no-session WS usage still works."""
    try:
        db = session_factory()
        try:
            session = repo.get_session(db, session_id)
            return session is not None and session.status == "active"
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to check session status for %s", session_id)
        return False


def _persist_reading(
    session_factory: Callable, session_id: str, reading: dict, per_vital_confidence: Optional[dict], provenance: str
) -> None:
    """A DB error here must never kill the stream — log and move on."""
    try:
        db = session_factory()
        try:
            repo.save_reading(
                db,
                session_id,
                reading,
                confidence=_mean_confidence(per_vital_confidence),
                provenance=provenance,
                per_vital_confidence=per_vital_confidence,
            )
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to persist reading for session %s", session_id)


def _persist_alert(session_factory: Callable, session_id: str, alert: dict) -> None:
    try:
        db = session_factory()
        try:
            repo.save_alert(db, session_id, alert)
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to persist alert for session %s", session_id)


def _persist_flagged(session_factory: Callable, session_id: str, flagged_data: dict) -> Optional[dict]:
    try:
        db = session_factory()
        try:
            saved = repo.save_flagged(db, session_id, flagged_data)
            return saved.model_dump(by_alias=True)
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to persist flagged reading for session %s", session_id)
        return None


def _ack_alert_in_db(session_factory: Callable, alert_id: str) -> None:
    try:
        db = session_factory()
        try:
            repo.ack_alert(db, alert_id)
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to acknowledge alert %s", alert_id)


async def send_loop(
    send_json,
    source: VitalsSource,
    throttle: AlertThrottle,
    history: Deque[dict],
    session_id: Optional[str] = None,
    session_factory: Optional[Callable] = None,
    confirmed_state: Optional[Dict[str, FieldState]] = None,
) -> None:
    """Consumes `source.stream()`, running every raw frame through
    app.validation.reconcile.reconcile() BEFORE emitting anything — the
    'reading' envelope is always the reconciled, complete (no nulls)
    VitalReading, never the raw OCR output. Pushes a 'flagged' envelope per
    entry reconcile() produced, and an 'alert' envelope per non-throttled
    check_alerts() hit (evaluated against the reconciled reading, not the
    raw one). `send_json` is duck-typed so this is directly unit-testable.

    session_id/session_factory are optional (default None): when both are
    provided, persistence is attempted each tick but gated by
    `_session_is_active` (skipped — not erroring — for an unknown/inactive
    session), isolated in its own try/except so a DB error never kills the
    stream. Leaving them unset keeps this streaming-only, same as S6.

    confirmed_state is optional (default None): auto-seeded from the first
    frame's timestamp if not provided — same backward-compatible pattern as
    session_id/session_factory, so existing 4-positional-arg call sites
    (S6's tests) keep working unchanged.
    """
    persist_eligible = session_id is not None and session_factory is not None

    async for frame in source.stream():
        if confirmed_state is None:
            confirmed_state = initial_confirmed_state(frame.reading.get("timestamp") or int(time.time() * 1000))

        reading, confirmed_state, flagged_entries = reconcile(frame.reading, frame.per_vital_confidence, confirmed_state)
        history.append(reading)

        should_persist = persist_eligible and _session_is_active(session_factory, session_id)

        await send_json(
            {
                "type": "reading",
                "reading": reading,
                "confidence": frame.per_vital_confidence,
                "provenance": frame.provenance,
            }
        )
        if should_persist:
            _persist_reading(session_factory, session_id, reading, frame.per_vital_confidence, frame.provenance)

        for alert_data in check_alerts(reading):
            if throttle.should_emit(alert_data["vitalType"], alert_data["severity"]):
                alert = build_alert(alert_data)
                await send_json({"type": "alert", "alert": alert})
                if should_persist:
                    _persist_alert(session_factory, session_id, alert)

        for flagged_data in flagged_entries:
            saved = _persist_flagged(session_factory, session_id, flagged_data) if should_persist else None
            envelope_flagged = saved if saved is not None else {**flagged_data, "id": _ephemeral_id("FLAG")}
            await send_json({"type": "flagged", "flagged": envelope_flagged})


async def receive_loop(websocket: WebSocket, session_factory: Optional[Callable] = None) -> None:
    """Handles client -> server messages. NIBP measurement logic stays a
    minimal stub per the task; ack_alert persists when a session_factory is
    provided (defaults to None, same no-persistence behavior as S6)."""
    while True:
        message = await websocket.receive_json()
        msg_type = message.get("type")

        if msg_type == "trigger_nibp":
            await websocket.send_json({"type": "nibp_measuring"})
        elif msg_type == "ack_alert":
            alert_id = message.get("id")
            if session_factory is not None and alert_id is not None:
                _ack_alert_in_db(session_factory, alert_id)
            await websocket.send_json({"type": "alert_acknowledged", "id": alert_id})


@router.websocket("/ws/vitals/{session_id}")
async def vitals_ws(
    websocket: WebSocket,
    session_id: str,
    source: str = "synthetic",
    dataset: Optional[str] = None,
    interval: float = 1.0,
    seed: Optional[int] = None,
) -> None:
    await websocket.accept()

    try:
        vitals_source = ReplaySource(mode=source, interval=interval, dataset_dir=dataset, seed=seed)
    except ValueError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close()
        return

    throttle = AlertThrottle()
    history: Deque[dict] = deque(maxlen=MAX_HISTORY)

    send_task = asyncio.create_task(
        send_loop(websocket.send_json, vitals_source, throttle, history, session_id=session_id, session_factory=SessionLocal)
    )
    receive_task = asyncio.create_task(receive_loop(websocket, session_factory=SessionLocal))

    try:
        done, pending = await asyncio.wait({send_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        receive_task.cancel()
