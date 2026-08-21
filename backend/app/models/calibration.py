from typing import Dict, Optional

from pydantic import Field

from app.models.base import CamelModel

# The six vitals a profile can locate -- same set read_frame.VITALS defines,
# duplicated here (not imported) because app.models must stay import-clean
# of app.pipeline (Pydantic schema layer shouldn't depend on the pipeline
# it's describing).
VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")


class NormalizedBox(CamelModel):
    """An axis-aligned ROI box in NORMALIZED [0, 1] coordinates, relative to
    the calibration profile's own reference_width/reference_height (see
    CalibrationProfile below) -- NOT raw pixels of whatever frame happens to
    be live right now.

    Why normalized, not pixel: a profile calibrated at one camera
    resolution/viewport must still locate the same six fields when a later
    frame arrives at a different resolution or aspect ratio (M5.2 Phase 6 --
    a phone's getUserMedia resolution can change between page loads, a
    screen-share's captured tab can resize). x*frame_w, y*frame_h,
    w*frame_w, h*frame_h recovers the correct pixel box at ANY frame size
    with the same aspect ratio as the reference; see
    app.pipeline.calibrated_roi for the actual mapping and its aspect-ratio
    drift guard.
    """

    x: float
    y: float
    w: float
    h: float


class CalibrationFieldMeta(CamelModel):
    """Per-field bookkeeping the Verify step (M5.2 Phase 2/7) records. Never
    consulted by reconcile() or any confidence gate -- rules.py's
    CONFIDENCE_MEDIUM_MIN stays the only thing that decides whether a LIVE
    reading is trusted. `verified` exists purely to block Save: a box the
    operator never actually watched OCR correctly must not silently become
    part of a saved profile (see docs/M5_2_REAL_CALIBRATION_REPORT.md sec 8,
    "Verify blocks Save").

    M5.7.3: `verified = True` means, precisely, "the operator visually
    confirmed this candidate against the monitor during calibration" -- an
    OPERATOR-CONFIRMATION fact, recorded by a click
    (src/features/calibration/fieldVerifyState.ts's buildFieldMeta is the
    only place that sets it on the frontend). It does NOT mean, and must
    never be read as meaning, "the OCR/burst-verification pipeline
    independently reached production confidence" -- `verified_confidence`
    carries whatever OCR's own mean confidence happened to be (which may be
    low, or the field may not even have been burst-stable) purely as
    diagnostic context, never as a claim of machine trust. The two are
    intentionally decoupled: an operator can confirm a low-confidence or
    burst-unstable candidate they can see is correct, and `verified` is
    still the only signal any gate (Save, here; nothing on the live path)
    may treat as trust."""

    verified: bool = False
    verified_value: Optional[str] = None
    verified_confidence: Optional[float] = None


class CalibrationProfile(CamelModel):
    """Persisted answer to "where do the six vital fields sit on THIS
    camera's view of THIS monitor" -- established once by an operator
    (M5.2) rather than inferred by a detector that has never fired on real
    data (see docs/ARCHITECTURE.md's case against detect_screen()).

    Deliberately NOT session-scoped. The pre-M5.2 CalibrationPage is
    reachable from StartPage BEFORE any session exists ("Setup Camera" on
    step 1 of case creation), and the product claim is "calibrate once,
    then every case reuses it" -- not "recalibrate every case". theatre_id/
    camera_id are optional hooks for a future multi-camera/multi-theatre
    deployment; today's single-active-profile model (app.db.repo's
    save_calibration_profile/get_active_calibration_profile) is the
    smallest thing that satisfies this milestone's actual demo scope (one
    camera, one monitor) -- see the M5.2 report sec 6 for why a richer
    per-camera registry was considered and deliberately deferred.

    NOTE on what this schema does NOT contain: an earlier version of this
    model (pre-M5.2) carried a `homography` field that
    docs/ARCHITECTURE.md's own audit found was "referenced by nothing" --
    dead weight nobody computed or consumed. M5.2 does not repeat that
    mistake: it reproduces exactly the "calibrated box, no tracking" mode
    EVIDENCE.md sec 6 measured (a fixed operator-drawn box reused on every
    later frame, no per-frame homography/keypoint re-estimation), so a
    homography/screen-quad field would again be unused by anything this
    milestone ships. Per-frame layout tracking (ORB keypoints anchored on
    the monitor's static chrome) is exactly the capability ROADMAP.md
    scopes to M5.3; when that lands, its anchor data belongs in a NEW field
    added then, once something actually consumes it -- not speculatively
    added now. See M5_2 report sec 3 and sec 19.

    M5.3 UPDATE. That consumer now exists (app.pipeline.layout_tracker), so
    the two fields below were added under exactly the condition M5.2 set.
    Note what they are NOT: no homography, no keypoints, and no descriptors
    live in this schema. The tracking anchor is the REFERENCE FRAME ITSELF,
    stored as image bytes in its own table (app.db.models.
    CalibrationReferenceFrameRow) and re-featurised at load time. Persisting
    descriptors instead would have frozen the schema to one ORB
    parameterisation -- unable to be re-derived if nfeatures or the working
    resolution changes, and impossible for a human to audit against the
    question that actually matters ("is this the frame the operator
    calibrated on?"). What lives here is only the metadata needed to answer
    that question without loading a few hundred KB of JPEG.
    """

    id: str
    theatre_id: Optional[str] = None
    camera_id: Optional[str] = None
    layout_id: str = "default"
    version: int = 1

    # The frame the roi_boxes below were normalized against. Required to
    # interpret them at all -- a NormalizedBox's numbers are meaningless
    # without knowing the aspect ratio they were drawn relative to.
    reference_width: int
    reference_height: int

    roi_boxes: Dict[str, NormalizedBox]
    field_meta: Dict[str, CalibrationFieldMeta] = Field(default_factory=dict)

    created_at: float
    updated_at: float
    is_active: bool = True

    # M5.3 tracking metadata. Both default to the pre-M5.3 answer, so every
    # profile saved by M5.2 deserialises unchanged and runs untracked --
    # which is also the documented rollback state.
    has_reference_frame: bool = False
    reference_frame_sha256: Optional[str] = None
