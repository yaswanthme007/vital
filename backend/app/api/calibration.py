"""M5.2: calibration profile lifecycle -- verify a CANDIDATE box set against
a live frame (no persistence), save a validated profile (activating it),
fetch the active profile, and invalidate it. See
docs/M5_2_REAL_CALIBRATION_REPORT.md for the full design.

Deliberately reuses app.pipeline.read_frame.read_frame() unmodified for the
verify step (via a roi_extractor closure over the CANDIDATE boxes) rather
than re-implementing a parallel OCR call -- the same "swap the ROI stage,
keep everything downstream identical" contract M3/M5.2's other pipeline
seams already follow. M5.1's OCR configuration is therefore exercised
byte-for-byte during calibration, not a second, drifting copy of it.
"""

import io
from typing import Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
from sqlalchemy.orm import Session as OrmSession

from app.db import repo
from app.db.session import SessionLocal
from app.models.base import CamelModel
from app.models.calibration import CalibrationFieldMeta, CalibrationProfile, NormalizedBox
from app.pipeline.burst_verify import FieldBurstResult, verify_burst
from app.pipeline.calibrated_roi import extract_rois_from_boxes, pad_roi_boxes
from app.pipeline.calibration_validate import validate_profile
from app.pipeline.ocr import NibpValue
from app.pipeline.read_frame import VITALS, read_frame

router = APIRouter(prefix="/api/calibration")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class VerifyCandidateRequest(CamelModel):
    """Shape of the JSON `payload` form field on POST /verify."""

    reference_width: int
    reference_height: int
    roi_boxes: Dict[str, NormalizedBox]


class SaveCalibrationRequest(CamelModel):
    theatre_id: Optional[str] = None
    camera_id: Optional[str] = None
    layout_id: str = "default"
    reference_width: int
    reference_height: int
    roi_boxes: Dict[str, NormalizedBox]
    field_meta: Dict[str, CalibrationFieldMeta] = {}


def _decode_upload(contents: bytes) -> np.ndarray:
    try:
        return np.array(Image.open(io.BytesIO(contents)).convert("RGB"))
    except UnidentifiedImageError:
        raise HTTPException(status_code=422, detail="Uploaded file is not a readable image")


@router.post("/verify")
async def verify_candidate(payload: str = Form(...), file: UploadFile = File(...)) -> dict:
    """Runs the real OCR pipeline against ONE candidate box (or several) on
    ONE captured frame -- no persistence. This is the mechanism behind
    Phase 2/7's "Verify OCRs every drawn box, operator confirms before
    Save": the frontend calls this once per box (or in a batch) as the
    operator draws/adjusts it, and only marks a field's CalibrationFieldMeta
    verified=True once the operator has looked at the returned value and
    confirmed it. Nothing here writes to the database.

    M5.7.1: `skip_screen_detection=True` -- these are always operator-drawn
    boxes normalized against the raw uploaded frame (see
    app.pipeline.calibrated_roi's module docstring), never against whatever
    detect_screen() may or may not rectify a given frame to. Running
    detect_screen() here was the root cause of intermittent per-field
    verify failures: its quad search is nondeterministic frame-to-frame on a
    real camera, so a frame where it happened to fire silently remapped
    every box onto a different pixel grid than the one it was drawn on. See
    docs/M5_7_1_PRODUCT_FLOW_AND_CALIBRATION_FIX.md for the full
    investigation.

    Also pads the candidate boxes with the SAME WIDTH_SAFETY_PAD_FRACTION
    save_profile() applies (calibrated_roi.pad_roi_boxes) -- previously
    Verify ran against the tight, as-drawn box while Save (and therefore
    every later live read) ran against the padded one, so a value that
    verified here could read differently in production. Verify now shows
    the geometry that will actually be active after Save.

    Returns `diagnostics` per field (raw/matched OCR text, or None when the
    box resolved to no crop at all) so a failed field can be explained to
    the operator instead of just showing `-`."""
    try:
        request = VerifyCandidateRequest.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid calibration payload: {exc}")

    contents = await file.read()
    img = _decode_upload(contents)
    h, w = img.shape[:2]

    padded_boxes = pad_roi_boxes(request.roi_boxes)

    def _extractor(image: np.ndarray):
        return extract_rois_from_boxes(image, padded_boxes)

    diagnostics: Dict[str, Optional[object]] = {}
    reading, confidence = read_frame(
        img, roi_extractor=_extractor, diagnostics=diagnostics, skip_screen_detection=True
    )
    diagnostics_out = {
        vital: {"rawText": diag.raw_text, "matchedText": diag.matched_text} if diag is not None else None
        for vital, diag in diagnostics.items()
    }
    return {
        "reading": reading,
        "confidence": confidence,
        "frameWidth": w,
        "frameHeight": h,
        "diagnostics": diagnostics_out,
    }


def _format_display_value(vital: str, value: object) -> Optional[str]:
    """Mirrors CalibrationPage.tsx's own formatVerifyValue -- NIBP shows as
    'sys/dia' (mean is never displayed, see burst_verify.py's
    _nibp_match_key docstring for why that's also what's compared for
    consensus), everything else as a plain number. Used only for the
    burst.field[vital].bestGuess* informational text -- the authoritative
    machine-readable value lives in `reading`/`confidence` exactly like the
    single-frame /verify response."""
    if value is None:
        return None
    if vital == "nibp":
        if isinstance(value, NibpValue) and value.systolic is not None and value.diastolic is not None:
            return f"{value.systolic:g}/{value.diastolic:g}"
        return None
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


@router.post("/verify-burst")
async def verify_burst_candidate(payload: str = Form(...), files: List[UploadFile] = File(...)) -> dict:
    """M5.7.2: the multi-frame version of /verify. Captures several REAL,
    independently-captured frames of the same live setup (the frontend
    spaces them ~150-250ms apart so each is a genuinely different camera
    read, not the same buffer sampled twice) and only reports a field as
    verified when a value is CONSISTENTLY reproduced across them -- see
    app.pipeline.burst_verify's module docstring for the full design and,
    critically, why this is not simply "vote across N frames" (that alone
    has a known blind spot -- a systematically bad crop reproduces the SAME
    wrong value every frame, which a naive vote would read as agreement).

    Response shape is the single-frame /verify response (reading/confidence/
    diagnostics -- so any UI that already knows how to render that keeps
    working unchanged) PLUS an additive `burst` object per field with
    agreement/sampleCount/stable/usedVariant/bestGuess*, so the UI can show
    'stable reading found: N of M frames agreed' instead of a bare number.

    A field that could not be stabilized reports `reading[vital] = null` --
    exactly the single-frame endpoint's existing "could not read" contract
    -- never an invented/forced value, even though burst.field[vital] still
    carries the best-guess diagnostics so the operator can be told WHY."""
    try:
        request = VerifyCandidateRequest.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid calibration payload: {exc}")
    if not files:
        raise HTTPException(status_code=422, detail="At least one frame is required")

    frames: List[np.ndarray] = []
    frame_w = frame_h = 0
    for f in files:
        contents = await f.read()
        img = _decode_upload(contents)
        frame_h, frame_w = img.shape[:2]
        frames.append(img)

    padded_boxes = pad_roi_boxes(request.roi_boxes)
    field_results: Dict[str, FieldBurstResult] = verify_burst(frames, padded_boxes)

    reading: dict = {
        "hr": None, "spo2": None, "nibpSystolic": None, "nibpDiastolic": None,
        "nibpMean": None, "etco2": None, "temp": None, "rr": None,
    }
    confidence: Dict[str, float] = {}
    diagnostics_out: Dict[str, Optional[dict]] = {}
    burst_out: Dict[str, dict] = {}

    for vital in VITALS:
        r = field_results.get(vital)
        if r is None:
            continue
        confidence[vital] = r.confidence
        diagnostics_out[vital] = (
            {"rawText": r.raw_text, "matchedText": r.matched_text} if r.raw_text else None
        )
        if vital == "nibp":
            if r.value is not None and isinstance(r.value, NibpValue):
                reading["nibpSystolic"] = r.value.systolic
                reading["nibpDiastolic"] = r.value.diastolic
                reading["nibpMean"] = r.value.mean
        else:
            reading[vital] = r.value
        burst_out[vital] = {
            "agreement": r.agreement_pct,
            "sampleCount": r.sample_count,
            "frameCount": r.frame_count,
            "stable": r.stable,
            # M5.7.2 hotfix: True when `stable` was reached via the
            # corroborated-recovery tier (strong-majority agreement at the
            # lower, still-evidence-backed confidence floor) rather than
            # full confidence on every agreeing sample -- lets the UI say
            # "one noisy frame was discarded" honestly instead of implying
            # every capture agreed cleanly at high confidence.
            "recovered": r.recovered,
            # Only meaningful when stable is False -- see
            # burst_verify.FieldBurstResult.unstable_reason's docstring for
            # the possible values ("no_reading" | "low_agreement" |
            # "geometry" | "low_confidence"). Lets the frontend distinguish
            # "hold the camera steady and try again" from "the box geometry
            # itself looks wrong, redraw it" instead of always suggesting a
            # redraw.
            "unstableReason": r.unstable_reason,
            "usedVariant": r.used_variant,
            "bestGuessValue": _format_display_value(vital, r.best_guess_value),
            "bestGuessAgreement": r.best_guess_agreement_pct,
        }

    return {
        "reading": reading,
        "confidence": confidence,
        "frameWidth": frame_w,
        "frameHeight": frame_h,
        "diagnostics": diagnostics_out,
        "burst": {"frameCount": len(frames), "field": burst_out},
    }


@router.post("", status_code=201)
def save_profile(body: SaveCalibrationRequest, db: OrmSession = Depends(get_db)) -> dict:
    """Validates (Phase 7 geometry checks + "every field was Verified"),
    then persists and activates the profile. A profile that fails
    validation is rejected with 422 and the specific reasons -- it is never
    partially saved.

    Applies WIDTH_SAFETY_PAD_FRACTION (app.pipeline.calibrated_roi) to every
    box BEFORE validating/saving -- the operator draws (and Verifies) the
    box tight to what they currently see, exactly as ROADMAP.md instructs,
    and this adds the evidence-derived safety margin automatically rather
    than asking the operator to remember to over-draw. See
    calibrated_roi.WIDTH_SAFETY_PAD_FRACTION's docstring for the measured
    justification."""
    padded_boxes = pad_roi_boxes(body.roi_boxes)

    candidate = CalibrationProfile(
        id="pending",
        theatre_id=body.theatre_id,
        camera_id=body.camera_id,
        layout_id=body.layout_id,
        reference_width=body.reference_width,
        reference_height=body.reference_height,
        roi_boxes=padded_boxes,
        field_meta=body.field_meta,
        created_at=0,
        updated_at=0,
    )
    errors = validate_profile(candidate)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    save_data = body.model_dump()
    save_data["roi_boxes"] = {v: b.model_dump() for v, b in padded_boxes.items()}
    saved = repo.save_calibration_profile(db, save_data)
    return saved.model_dump(by_alias=True)


@router.get("/active")
def get_active(db: OrmSession = Depends(get_db)) -> dict:
    profile = repo.get_active_calibration_profile(db)
    if profile is None:
        raise HTTPException(status_code=404, detail="No active calibration profile")
    return profile.model_dump(by_alias=True)


@router.put("/active/reference-frame", status_code=200)
async def attach_reference_frame(file: UploadFile = File(...), db: OrmSession = Depends(get_db)) -> dict:
    """M5.3. Attaches the REFERENCE FRAME -- the exact frame the active
    profile's boxes were drawn and Verified against -- enabling per-frame
    layout tracking for that profile.

    A SEPARATE call rather than a field on POST /api/calibration, because
    that endpoint's JSON contract is depended on by existing callers and
    tests, and because "profile saved, frame not yet attached" is a SAFE
    intermediate state, not a broken one: a profile with no reference frame
    simply runs untracked, which is exactly M5.2's shipped behaviour. The
    frontend reports the resulting hasReferenceFrame back to the operator so
    tracking is never silently believed to be on when it is not.

    WHICH FRAME THIS MUST BE. The frame POST /verify ran against. The
    calibration UI clears every field's verified flag whenever a box is
    moved or resized, so the frame the operator confirmed each reading on is
    necessarily the frame the saved boxes describe -- uploading any other
    frame would silently anchor tracking to geometry the boxes were never
    drawn against."""
    profile = repo.get_active_calibration_profile(db)
    if profile is None:
        raise HTTPException(status_code=404, detail="No active calibration profile")

    contents = await file.read()
    img = _decode_upload(contents)
    h, w = img.shape[:2]
    if w != profile.reference_width or h != profile.reference_height:
        # The boxes are normalized against reference_width/height; a frame of
        # different dimensions is evidence this is not the frame they were
        # drawn on. Rejecting is the whole point of Phase 3's "do not
        # accidentally use a different frame".
        raise HTTPException(
            status_code=422,
            detail=(
                f"Reference frame is {w}x{h} but the profile's boxes were calibrated against "
                f"{profile.reference_width}x{profile.reference_height}. This is not the frame "
                f"those boxes were drawn on."
            ),
        )

    digest = repo.save_calibration_reference_frame(
        db, profile.id, contents, file.content_type or "image/jpeg", w, h
    )
    return {"profileId": profile.id, "referenceFrameSha256": digest, "width": w, "height": h,
            "trackingEnabled": True}


@router.get("/active/reference-frame")
def get_active_reference_frame(db: OrmSession = Depends(get_db)):
    profile = repo.get_active_calibration_profile(db)
    if profile is None:
        raise HTTPException(status_code=404, detail="No active calibration profile")
    row = repo.get_calibration_reference_frame(db, profile.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Active calibration profile has no reference frame")
    return Response(content=row.image_bytes, media_type=row.mime,
                    headers={"X-Reference-Frame-Sha256": row.sha256})


@router.delete("/active", status_code=204)
def invalidate_active(db: OrmSession = Depends(get_db)) -> None:
    """Operator-triggered escape hatch (Phase 4/12): explicitly stop using
    the current profile, e.g. the camera or monitor visibly changed. The
    live-camera path degrades to its default ROI_ENGINE on the very next
    WS connection -- never a crash, never a silent continuation on stale
    geometry."""
    repo.invalidate_active_calibration_profile(db)
