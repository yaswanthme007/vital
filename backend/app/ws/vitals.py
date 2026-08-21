import asyncio
import io
import logging
import os
import random
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from PIL import Image

from app.alerts.rules import AlertThrottle, build_alert, check_alerts
from app.db import repo
from app.db.session import SessionLocal
from app.pipeline.calibrated_roi import make_extractor as make_calibrated_roi_extractor
from app.pipeline.calibrated_roi import reference_pixel_boxes
from app.pipeline.layout_tracker import LayoutTracker
from app.sources.base import VitalsSource
from app.sources.camera import CameraSource
from app.sources.frame_queue import clear_channel
from app.sources.replay import ReplaySource
from app.validation.live_corroboration import FieldEvidence, initial_evidence_state
from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile
from app.validation.temporal import TemporalFieldState, initial_temporal_state

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
    session_factory: Callable, session_id: str, reading: dict, per_vital_confidence: Optional[dict], provenance: str,
    source: Optional[str] = None, field_status: Optional[dict] = None,
) -> None:
    """A DB error here must never kill the stream — log and move on.

    source/field_status (M5.7, both optional, default None): passed straight
    through to repo.save_reading -- see that function and
    app.db.models.VitalReadingRow for what they mean. Both simply stay NULL
    on the row for any caller (or pre-M5.7 test) that omits them."""
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
                source=source,
                field_status=field_status,
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


class _PersistenceGate:
    """M5.7. Per-WS-connection cadence for the OBSERVED TIMELINE writes
    app.db.models.VitalReadingRow accumulates, independent of the LIVE
    STATE envelope send_loop pushes over the WebSocket on every processed
    frame (see send_loop's own docstring for that two-cadence split). Only
    ever constructed/consulted for a camera-sourced connection -- see
    send_loop's `source_tag` parameter.

    Fires (should_write returns True) the FIRST time it is asked
    (last_write_ms is None -- there is nothing yet to compare a 'change'
    against, and the very first genuine observation must never be
    withheld), then on any later tick where either >=MIN_INTERVAL_MS has
    elapsed since the last write, OR at least one of this tick's confirmed
    values differs from what was last written. A field CONFIRMED for the
    first time ever (e.g. NIBP's first real measurement) counts as
    'changed': it is simply absent from `last_values`, which compares as
    different from any real value, so it is never held back waiting for
    the 1s timer.

    Bounded by construction: should_write can be True on consecutive ticks
    only as fast as frames are actually processed (~1 per
    app.sources.camera.CameraSource's own interval + OCR cost, measured at
    ~3s in production -- see docs/VITAL_LIVE_CAMERA_TRACKING_ARCHITECTURE_
    ANALYSIS.md), and never faster than once per MIN_INTERVAL_MS regardless
    of how many values are changing at once -- a multi-hour case cannot
    spam the table."""

    MIN_INTERVAL_MS = 1000

    def __init__(self) -> None:
        self.last_write_ms: Optional[int] = None
        self.last_values: Dict[str, float] = {}

    def should_write(self, now_ms: int, confirmed_values: Dict[str, float]) -> bool:
        if self.last_write_ms is None:
            return True
        if now_ms - self.last_write_ms >= self.MIN_INTERVAL_MS:
            return True
        return any(self.last_values.get(field) != value for field, value in confirmed_values.items())

    def record(self, now_ms: int, confirmed_values: Dict[str, float]) -> None:
        self.last_write_ms = now_ms
        self.last_values.update(confirmed_values)


async def send_loop(
    send_json,
    source: VitalsSource,
    throttle: AlertThrottle,
    history: Deque[dict],
    session_id: Optional[str] = None,
    session_factory: Optional[Callable] = None,
    confirmed_state: Optional[Dict[str, FieldState]] = None,
    tracking_state: Optional["TrackingState"] = None,
    temporal_state: Optional[Dict[str, TemporalFieldState]] = None,
    source_tag: Optional[str] = None,
    persistence_gate: Optional["_PersistenceGate"] = None,
    corroboration_state: Optional[Dict[str, FieldEvidence]] = None,
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

    temporal_state (M5.4, optional, default None): a per-connection
    Dict[field, TemporalFieldState], auto-seeded via initial_temporal_state()
    on the first frame exactly like confirmed_state is, when
    TEMPORAL_CORROBORATION is enabled (see _temporal_corroboration_enabled
    below). Passed straight through into reconcile() and mutated there;
    left None entirely when the feature is off, which reproduces pre-M5.4
    reconcile() behaviour byte-for-byte (see app.validation.reconcile's own
    docstring on this parameter).

    source_tag (M5.7, optional, default None): the WS route's own `source`
    query-string value ('camera' | 'synthetic' | 'pipeline' | ...), threaded
    through purely so persisted rows can record WHERE they came from (see
    app.db.models.VitalReadingRow.source). Only 'camera' changes
    persistence BEHAVIOUR -- see this function's own docstring above.
    Scoped deliberately, not by oversight: this milestone's evidence (see
    docs/VITAL_LIVE_CAMERA_TRACKING_ARCHITECTURE_ANALYSIS.md) is
    specifically that CAMERA-sourced rows could silently be fabricated
    held/baseline values, camera is the only source a real operator's
    laptop ever drives, and every synthetic/replay call site (including
    frozen milestone evidence -- M4.3 Test H, M4.5 Config B, M5.4's own
    temporal-corroboration persistence test) already asserts on the exact
    pre-M5.7 full-reading-every-tick shape for those sources.

    corroboration_state (M5.8, optional, default None): auto-instantiated
    (via initial_evidence_state()) on the first frame of a CAMERA
    connection, exactly like persistence_gate below, and never for any
    other source. Passing it into reconcile() switches the camera path from
    "one frame at >=70% confidence confirms" to "several recent frames must
    agree, all clean" -- see app.validation.live_corroboration for the
    measured reason. Together with allow_baseline=False (also camera-only)
    this is what makes the LIVE STATE and the OBSERVED TIMELINE contain
    only values the camera genuinely read off the monitor.

    persistence_gate (M5.7, optional, default None): auto-instantiated
    (a fresh _PersistenceGate) the same way confirmed_state/temporal_state
    are auto-seeded above, but only when source_tag == 'camera'. See
    _PersistenceGate for the cadence it owns. Never constructed or
    consulted for any other source_tag.

    M5.4.1: frame.crop_suspicious (app.sources.base.Frame) is passed into
    reconcile() as per_vital_crop_suspicious on every tick, not just when
    temporal corroboration is enabled -- it is free to pass (an empty dict
    for non-camera sources) and reconcile() only ever consults it inside
    the temporal-corroboration branch, which itself only runs when
    temporal_state is not None. See app.validation.crop_integrity and
    app.validation.temporal for what it does once inside that branch.

    M5.7 -- LIVE STATE vs OBSERVED TIMELINE. Every tick now calls
    reconcile() with a fresh field_status out-param, and the 'reading' WS
    envelope grows two additive keys: `fieldStatus` (confirmed/held/
    baseline per field, straight from reconcile()) and `lastConfirmedAt`
    (per field, the timestamp of the last tick that field was genuinely
    confirmed -- held fields keep advancing display value without their
    timestamp moving). That envelope is the LIVE STATE: it is sent on
    every processed frame regardless of source, exactly as before.

    What changed is what gets WRITTEN to SQLite as the OBSERVED TIMELINE,
    and only for a camera-sourced connection (source_tag == 'camera'):
    only fields field_status marked 'confirmed' this tick are persisted
    (missing fields simply absent -> NULL columns on that row), and only
    on a tick persistence_gate.should_write() approves (see that class).
    An all-held/all-baseline tick -- e.g. the monitor is occluded, or
    layout tracking has lost lock and calibrated_roi withheld every
    field -- writes NO row at all: a held/baseline value must never become
    a fabricated entry in the patient's observed history. Alerts are
    likewise evaluated only against this tick's confirmed values, for
    every source, not just camera -- a held or baseline reading can never
    raise a clinical alert.

    Every other source_tag (including the default None, which every
    pre-M5.7 call site implicitly passes) persists exactly as before this
    parameter existed: the full reconciled `reading` dict, written on
    every persist-eligible tick with no gate. This is a deliberate scoping
    decision -- see source_tag's own parameter docstring below for why.
    """
    persist_eligible = session_id is not None and session_factory is not None
    temporal_enabled = _temporal_corroboration_enabled()
    is_camera = source_tag == "camera"

    async for frame in source.stream():
        if confirmed_state is None:
            # M5.8: a camera connection starts with NO confirmed values at
            # all -- not DEFAULT_BASELINE. Every field reads 'unknown' and
            # displays nothing until the camera genuinely observes it. See
            # reconcile()'s allow_baseline docstring for the defect this
            # closes. Non-camera sources keep their seeded baseline.
            confirmed_state = (
                {}
                if is_camera
                else initial_confirmed_state(frame.reading.get("timestamp") or int(time.time() * 1000))
            )
        if temporal_enabled and temporal_state is None:
            temporal_state = initial_temporal_state()
        if is_camera and persistence_gate is None:
            persistence_gate = _PersistenceGate()
        if is_camera and corroboration_state is None:
            corroboration_state = initial_evidence_state()

        field_status: Dict[str, str] = {}
        reading, confirmed_state, flagged_entries = reconcile(
            frame.reading, frame.per_vital_confidence, confirmed_state, temporal_state=temporal_state,
            per_vital_crop_suspicious=frame.crop_suspicious, field_status=field_status,
            corroboration=corroboration_state, allow_baseline=not is_camera,
        )
        history.append(reading)

        should_persist = persist_eligible and _session_is_active(session_factory, session_id)
        now_ms = reading.get("timestamp") or int(time.time() * 1000)
        # The subset of this tick's fields reconcile() genuinely confirmed --
        # a real OBSERVATION, not a display value carried over from a prior
        # tick. Used for both persistence (below) and alerts.
        confirmed_values = {f: reading[f] for f, status in field_status.items() if status == "confirmed"}

        envelope = {
            "type": "reading",
            "reading": reading,
            "confidence": frame.per_vital_confidence,
            "provenance": frame.provenance,
            # M5.7: additive LIVE STATE metadata -- see this function's own
            # docstring. Always present (not gated on a feature flag) since
            # reconcile() computes field_status unconditionally now; a
            # client that predates this is unaffected because it simply
            # never reads these two keys.
            "fieldStatus": field_status,
            "lastConfirmedAt": {f: confirmed_state[f].timestamp for f in confirmed_state},
        }
        # M5.3: additive only. Absent unless layout tracking is actually
        # running, so existing clients (and every pre-M5.3 test asserting on
        # this envelope) are unaffected, and a client is never told "unlocked"
        # by a pipeline that isn't tracking at all.
        tracking_envelope = tracking_state.envelope() if tracking_state is not None else None
        if tracking_envelope is not None:
            envelope["tracking"] = tracking_envelope
        await send_json(envelope)

        if should_persist:
            if is_camera:
                # M5.7 OBSERVED TIMELINE: only genuine observations, only on
                # a tick the persistence gate approves. An all-held/all-
                # baseline tick has an empty confirmed_values and writes
                # nothing -- see _PersistenceGate and this function's
                # docstring for why this branch is camera-only.
                if confirmed_values and persistence_gate.should_write(now_ms, confirmed_values):
                    _persist_reading(
                        session_factory, session_id,
                        {**confirmed_values, "timestamp": now_ms},
                        frame.per_vital_confidence, frame.provenance,
                        source=source_tag, field_status=field_status,
                    )
                    persistence_gate.record(now_ms, confirmed_values)
            else:
                # Pre-M5.7 behaviour, byte-for-byte: the full reconciled
                # reading, every persist-eligible tick, no gate. See
                # source_tag's parameter docstring for why this is scoped
                # to non-camera sources only.
                _persist_reading(
                    session_factory, session_id, reading, frame.per_vital_confidence, frame.provenance,
                    source=source_tag, field_status=field_status,
                )

        # M5.7: alerts fire only from genuinely CONFIRMED values, for every
        # source -- a held or baseline field must never raise a clinical
        # alert. Relies on check_alerts' existing None-skipping contract: a
        # field absent from confirmed_values is simply never compared.
        for alert_data in check_alerts(confirmed_values):
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


class TrackingState:
    """Carries the most recent TrackingResult from the ROI extractor (which
    runs deep inside read_frame, on a worker thread) out to send_loop, which
    puts the lock state on the wire. Single-writer/single-reader per
    connection, and the write happens-before the read: CameraSource awaits
    its to_thread() call, so the frame it yields was produced by the same
    extractor invocation that set this."""

    def __init__(self) -> None:
        self.latest = None
        self.enabled = False

    def observe(self, result) -> None:
        self.latest = result

    def envelope(self) -> Optional[dict]:
        """The 'tracking' key added to each reading. None when tracking is
        not running at all, so the field is simply absent rather than
        reporting a misleading 'unlocked'."""
        if not self.enabled:
            return None
        if self.latest is None:
            return {"enabled": True, "locked": False, "status": "pending"}
        r = self.latest
        return {
            "enabled": True,
            "locked": r.ok,
            "status": r.status.value,
            "inliers": r.n_inliers,
            "scale": round(r.scale, 3),
            "rotationDeg": round(r.rotation_deg, 2),
            "reasons": list(r.reject_reasons),
        }


def _tracking_enabled() -> bool:
    """M5.3 rollback lever. LAYOUT_TRACKING=off restores M5.2 exactly (static
    calibrated boxes); 'auto' (the default) tracks whenever the active
    profile actually carries a reference frame."""
    return os.environ.get("LAYOUT_TRACKING", "auto").strip().lower() != "off"


def _temporal_corroboration_enabled() -> bool:
    """M5.4 rollback lever / opt-in switch. Defaults OFF and, per
    docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md's Phase 6 GO/NO-GO, SHOULD
    STAY OFF: held-out validation (replaying the same mechanism against
    Dataset B's harder second reference frame, frozen_B[sample_0011]) found
    a real confidently-wrong regression -- a systematically truncated HR
    read ("83"/"84" clipped to "8") repeating at confidence 58-66%, inside
    this feature's own corroboration band, with no confidence floor able to
    separate it from genuine corroborations measured in the same arm. See
    app.validation.temporal's TEMPORAL_AGREEMENT_MIN_RUN comment and the
    M5.4 report for the full reconstruction. The mechanism, its tests and
    its eval harness are kept in the codebase (isolated, fail-closed,
    reversible, and useful evidence for whoever revisits this) but this
    milestone's own verdict is NO-GO for enabling it; TEMPORAL_CORROBORATION=on
    exists for experimentation/further evidence-gathering, not as a
    supported production mode. Leaving this off (or unset) reproduces
    pre-M5.4 reconcile() behaviour exactly -- temporal_state stays None all
    the way through."""
    return os.environ.get("TEMPORAL_CORROBORATION", "off").strip().lower() == "on"


def _camera_roi_extractor(session_factory: Callable, tracking_state: Optional["TrackingState"] = None):
    """M5.2: looks up the currently-ACTIVE CalibrationProfile and binds it
    into a RoiExtractor for CameraSource. Returns None (read_frame()'s own
    ROI_ENGINE-selected default -- unchanged, M5.1's Tier-1 colour path
    unless ROI_ENGINE says otherwise) when no profile has been calibrated
    yet, or on any DB error -- a calibration lookup failure must never
    block the live-camera path from starting at all, same posture as this
    module's other _persist_* helpers.

    M5.3: if that profile carries a reference frame, a LayoutTracker is built
    ONCE here (per WebSocket connection, never per frame -- feature
    extraction on the reference costs ~50-500ms and must not land in the 1Hz
    loop) and passed to the extractor, which then re-anchors the calibrated
    boxes on every frame. A profile with no reference frame, or a failure to
    build the tracker, degrades to the M5.2 static path -- never to a crash
    and never to an untrustworthy transform."""
    try:
        db = session_factory()
        try:
            profile = repo.get_active_calibration_profile(db)
            reference = (
                repo.get_calibration_reference_frame(db, profile.id)
                if profile is not None and _tracking_enabled()
                else None
            )
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to look up the active calibration profile")
        return None
    if profile is None:
        return None

    tracker = None
    if reference is not None:
        try:
            img = np.array(Image.open(io.BytesIO(reference.image_bytes)).convert("RGB"))
            tracker = LayoutTracker.from_reference_image(
                img, exclude_boxes=list(reference_pixel_boxes(profile).values())
            )
            logger.info(
                "layout tracking enabled for profile %s (reference sha256=%s, %d keypoints)",
                profile.id, reference.sha256[:12], tracker.n_reference_keypoints,
            )
        except Exception:
            # A broken reference image must not take the camera path down; it
            # degrades to M5.2's static calibrated boxes.
            logger.exception("Failed to build a layout tracker; falling back to static calibrated boxes")
            tracker = None

    if tracking_state is not None:
        tracking_state.enabled = tracker is not None

    return make_calibrated_roi_extractor(
        profile,
        tracker=tracker,
        on_tracking_result=(tracking_state.observe if tracking_state is not None else None),
    )


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

    # M5.3: one TrackingState per connection, matching the one-tracker-per-
    # connection lifetime below. Stays disabled (and therefore absent from
    # every envelope) for non-camera sources and for profiles with no
    # reference frame.
    tracking_state = TrackingState()

    try:
        if source == "camera":
            # channel = session_id: the browser pushes frames to
            # POST /api/pipeline/push-frame/{session_id}, so each session's
            # live-camera feed is independent even with multiple concurrent
            # cases open (e.g. two demo laptops, or a rehearsal running
            # alongside the real thing).
            roi_extractor = _camera_roi_extractor(SessionLocal, tracking_state)
            # M5.7.1: a non-None roi_extractor here is always the calibrated
            # (operator-drawn-box) closure from make_extractor() -- see
            # _camera_roi_extractor's own docstring. Those boxes are
            # normalized against the raw frame, so detect_screen() must not
            # run ahead of them (read_frame()'s skip_screen_detection
            # docstring has the full root-cause explanation).
            vitals_source: VitalsSource = CameraSource(
                channel=session_id,
                interval=interval,
                roi_extractor=roi_extractor,
                skip_screen_detection=(roi_extractor is not None),
            )
        else:
            vitals_source = ReplaySource(mode=source, interval=interval, dataset_dir=dataset, seed=seed)
    except ValueError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close()
        return

    throttle = AlertThrottle()
    history: Deque[dict] = deque(maxlen=MAX_HISTORY)

    send_task = asyncio.create_task(
        send_loop(
            websocket.send_json, vitals_source, throttle, history,
            session_id=session_id, session_factory=SessionLocal, tracking_state=tracking_state,
            # M5.7: threads this connection's own ?source= query value
            # through so persisted rows carry a real source tag, and so
            # only the 'camera' path gets the confirmed-only/gated
            # persistence -- see send_loop's own docstring.
            source_tag=source,
        )
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
        if source == "camera":
            clear_channel(session_id)
