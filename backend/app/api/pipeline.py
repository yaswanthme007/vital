import io
import logging
from typing import Optional, Tuple

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.db import repo
from app.db.session import SessionLocal
from app.pipeline.calibrated_roi import make_extractor as make_calibrated_roi_extractor
from app.pipeline.read_frame import RoiExtractor, read_frame
from app.sources.frame_queue import push_frame

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _active_calibrated_extractor() -> Tuple[Optional[RoiExtractor], str]:
    """M5.6 production promotion. Returns (roi_extractor, roi_source).

    WHY THIS EXISTS. Before M5.6 this module's read-frame endpoint called a
    bare read_frame(img), which means it used read_frame()'s own
    ROI_ENGINE-selected default -- the Tier-1 colour path. M5.1 sec 14
    measured that path locating 0/17 fields on real photographed monitors,
    so the OCR Debug page produced nothing usable against exactly the input
    the rest of the product is now built around, while the live camera path
    (app.ws.vitals._camera_roi_extractor, validated end-to-end by M5.5) read
    the same monitor correctly. That was a real production inconsistency
    between two server paths, not a UI issue -- this closes it.

    WHAT IT DELIBERATELY DOES NOT DO. It does not change read_frame()'s
    ROI_ENGINE default. ROI_ENGINE='calibrated' RAISES unless
    CALIBRATION_PROFILE_PATH names a profile JSON on disk (see
    app.pipeline.read_frame._build_roi_extractor_from_env), which is never
    set in production, so flipping that default -- docs/ROADMAP.md M5.6's
    original wording -- would 500 this endpoint, break
    ReplaySource('pipeline'), and take down the camera WebSocket on any
    uncalibrated session. Promotion happens HERE, at the layer that already
    has a database session, exactly as the live camera path already does it.

    NO TRACKER, deliberately. LayoutTracker is built once per WebSocket
    connection and re-anchors boxes across a stream of frames; this endpoint
    is single-shot with no connection lifetime to own one. Tracking is
    therefore out of scope here -- this promotes M5.2's calibrated
    localization to this path, not M5.3's per-frame re-anchoring.

    FAIL-OPEN, never fatal. Any failure (no profile, DB error, unbuildable
    extractor) returns (None, 'default'), which reproduces this endpoint's
    exact pre-M5.6 behaviour. Same posture as
    app.ws.vitals._camera_roi_extractor's own except-and-degrade.
    """
    try:
        db = SessionLocal()
        try:
            profile = repo.get_active_calibration_profile(db)
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to look up the active calibration profile")
        return None, "default"

    if profile is None:
        return None, "default"

    try:
        return make_calibrated_roi_extractor(profile), "calibrated"
    except Exception:
        logger.exception("Failed to build a calibrated ROI extractor from the active profile")
        return None, "default"


@router.post("/pipeline/read-frame")
async def read_frame_endpoint(file: UploadFile = File(...)) -> dict:
    """Runs one uploaded camera frame through the real detect -> rectify ->
    ROI -> OCR pipeline (app.pipeline.read_frame) — the same code path the
    eval harness and replay pipeline source use, just fed a live frame
    instead of a pre-rendered/replayed one.

    M5.6: the ROI stage now prefers the database's ACTIVE CalibrationProfile
    when one exists, matching the live camera path, and falls back to
    read_frame()'s ROI_ENGINE default when it does not. The response says
    which one ran in `roiSource` ('calibrated' | 'default') so a reader is
    never left guessing which localization produced a number. See
    _active_calibrated_extractor above for the full rationale.

    Single-shot and RAW: returns the pipeline's output directly, with no
    reconcile()/validation, no persistence, no alerts. This is a debugging/
    calibration tool (OCR Debug page, Calibration's Verify step) — it is
    deliberately NOT the live-camera path. That path is CameraSource
    (app/sources/camera.py) via push-frame below + `?source=camera` on the
    WS, which runs every frame through send_loop -> reconcile() -> alerts ->
    persistence like every other source. Do not add a second caller of this
    endpoint that streams continuously in place of that.
    """
    contents = await file.read()
    try:
        img = np.array(Image.open(io.BytesIO(contents)).convert("RGB"))
    except UnidentifiedImageError:
        raise HTTPException(status_code=422, detail="Uploaded file is not a readable image")

    roi_extractor, roi_source = _active_calibrated_extractor()
    reading, confidence = read_frame(
        img, roi_extractor=roi_extractor, skip_screen_detection=(roi_source == "calibrated")
    )
    return {"reading": reading, "confidence": confidence, "roiSource": roi_source}


@router.post("/pipeline/push-frame/{channel}")
async def push_frame_endpoint(channel: str, file: UploadFile = File(...)) -> dict:
    """Ingestion side of CameraSource (app/sources/camera.py): the browser
    posts one camera frame at a time here; CameraSource.stream() (running
    inside the `?source=camera` WS connection's send_loop) drains whatever
    the latest pushed frame is on its own interval.

    Deliberately does NOT call read_frame()/reconcile() itself — this
    endpoint's only job is "hand the raw bytes to the queue". Running OCR
    here too would create a second, parallel path that never goes through
    reconcile()/alerts/persistence, defeating the point of routing camera
    frames through the WS's send_loop in the first place. `channel` is
    typically the session id, so concurrent sessions don't clobber each
    other's latest frame.
    """
    contents = await file.read()
    try:
        Image.open(io.BytesIO(contents)).verify()
    except UnidentifiedImageError:
        raise HTTPException(status_code=422, detail="Uploaded file is not a readable image")

    seq = push_frame(channel, contents)
    return {"channel": channel, "seq": seq}
