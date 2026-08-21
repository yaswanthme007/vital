import os
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from app.pipeline.detect import detect_screen
from app.pipeline.ocr import NibpValue, OcrDiagnostics, OcrEngine, TesseractEngine
from app.pipeline.roi import extract_rois_by_colour
from app.pipeline.types import VitalRoiResult
from app.validation.crop_integrity import crop_is_suspicious

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

RoiExtractor = Callable[[np.ndarray], Dict[str, Optional[VitalRoiResult]]]

_default_engine: Optional[OcrEngine] = None
_default_roi_extractor: Optional[RoiExtractor] = None


def get_default_engine() -> OcrEngine:
    """Lazy singleton so importing this module never touches Tesseract (or
    ONNX/onnxruntime) unless a frame is actually read.

    Tesseract (Tier-1) is the default in every case -- ONNX (Tier-2, S11) is
    opt-in only, via the OCR_ENGINE=onnx env var, and even then only if the
    trained model file actually exists. Merely having the model file present
    is deliberately NOT enough to switch the default; a silent switch would
    change production behaviour just because someone regenerated a model.
    """
    global _default_engine
    if _default_engine is None:
        _default_engine = _build_engine_from_env()
    return _default_engine


def _build_engine_from_env() -> OcrEngine:
    choice = os.environ.get("OCR_ENGINE", "tesseract").strip().lower()
    if choice in ("", "tesseract"):
        return TesseractEngine()
    if choice == "onnx":
        from app.pipeline.onnx_engine import OnnxDigitEngine, model_available

        if not model_available():
            raise FileNotFoundError(
                "OCR_ENGINE=onnx was requested but no trained model was found under models/. "
                "Train one first (see app/pipeline/onnx_engine.py's OnnxDigitEngine docstring) "
                "or unset OCR_ENGINE to fall back to Tesseract."
            )
        return OnnxDigitEngine()
    raise ValueError(f"Unknown OCR_ENGINE '{choice}'. Choose 'tesseract' or 'onnx'.")


def get_default_roi_extractor() -> RoiExtractor:
    """Lazy singleton, mirroring get_default_engine() exactly. ROI_ENGINE is
    orthogonal to OCR_ENGINE (M3, extending TIER2_RECOGNITION_SPIKE.md sec
    10's own design): this picks HOW a vital's region is located
    (tesseract/colour = Tier-1's fixed-hue ROI extraction, tier2 = M1.1
    candidate generation + M2's FieldCNN), OCR_ENGINE separately picks how
    the located crop is read. Any combination of {tesseract, tier2} x
    {tesseract, onnx} is valid.

    'tesseract' is the ROI_ENGINE default value's name per this milestone's
    spec, even though the Tier-1 ROI stage itself is colour-based, not
    Tesseract-based -- it names "the existing default pipeline" for
    consistency with the milestone brief's ROI_ENGINE=tesseract/tier2
    convention, not a literal engine choice at the ROI stage.
    """
    global _default_roi_extractor
    if _default_roi_extractor is None:
        _default_roi_extractor = _build_roi_extractor_from_env()
    return _default_roi_extractor


def _build_roi_extractor_from_env() -> RoiExtractor:
    choice = os.environ.get("ROI_ENGINE", "tesseract").strip().lower()
    if choice in ("", "tesseract"):
        return extract_rois_by_colour
    if choice == "tier2":
        from app.pipeline.field_classifier import model_available

        if not model_available():
            raise FileNotFoundError(
                "ROI_ENGINE=tier2 was requested but no trained field classifier was found under "
                "models/ (field_classifier.onnx). Train one first (see "
                "app/eval/tier2_field_dataset.py and app/eval/tier2_field_classifier.py) "
                "or unset ROI_ENGINE to fall back to the Tier-1 colour path."
            )
        from app.pipeline.tier2_roi import extract_rois_by_field_classifier

        return extract_rois_by_field_classifier
    if choice == "calibrated":
        # M5.2. This env-var path is for SCRIPTS/EVAL only (e.g.
        # app/eval/m5_2_calibration_eval.py) or a single-operator/single-
        # camera deployment that wants a server-wide default -- it loads
        # ONE fixed profile from disk at process start. The live product
        # path (app.ws.vitals._camera_roi_extractor, feeding
        # app.sources.camera.CameraSource) does NOT use this env var at
        # all: it looks up the DB's currently-ACTIVE CalibrationProfile
        # per WS connection instead, because a bare env var can't hold
        # "whichever profile the operator most recently saved" the way a
        # database row can. Both paths ultimately build their extractor
        # via app.pipeline.calibrated_roi.make_extractor() -- this is just
        # a second way to obtain the CalibrationProfile that function needs.
        from app.models.calibration import CalibrationProfile
        from app.pipeline.calibrated_roi import make_extractor

        profile_path = os.environ.get("CALIBRATION_PROFILE_PATH")
        if not profile_path:
            raise ValueError(
                "ROI_ENGINE=calibrated requires CALIBRATION_PROFILE_PATH to point at a saved "
                "CalibrationProfile JSON file (fetch one via GET /api/calibration/active, or see "
                "app/models/calibration.py for the shape). The live WS/camera path does not need "
                "this at all -- it reads the database's active profile directly."
            )
        with open(profile_path) as f:
            profile = CalibrationProfile.model_validate_json(f.read())
        return make_extractor(profile)
    raise ValueError(f"Unknown ROI_ENGINE '{choice}'. Choose 'tesseract', 'tier2', or 'calibrated'.")


def read_frame(
    img: np.ndarray,
    engine: Optional[OcrEngine] = None,
    roi_extractor: Optional[RoiExtractor] = None,
    crop_integrity: Optional[Dict[str, bool]] = None,
    diagnostics: Optional[Dict[str, Optional[OcrDiagnostics]]] = None,
    skip_screen_detection: bool = False,
) -> Tuple[dict, Dict[str, float]]:
    """Run the full S4 pipeline (detect -> rectify -> ROI) then OCR each
    vital. `engine` defaults to Tesseract but accepts anything implementing
    OcrEngine (S11's onnx digit engine). `roi_extractor` defaults to the
    ROI_ENGINE env var's choice (M3: Tier-1 colour ROI, or Tier-2 candidate
    generation + FieldCNN) but accepts anything matching
    extract_rois_by_colour's Dict[str, Optional[VitalRoiResult]] contract --
    this is the swap point both S11 and M3 use, and read_frame()'s own
    control flow below is otherwise completely unaware of which engine
    produced either its crops or its OCR reads.

    Returns (reading, confidences) -- UNCHANGED CONTRACT, every existing
    caller (app/api/pipeline.py, app/sources/camera.py, app/sources/replay.py,
    app/eval/harness.py) 2-unpacks this tuple and keeps working whether the
    ROI stage was Tier-1 or Tier-2:
      reading: VitalReading-shaped dict (camelCase keys matching the
        frontend/Pydantic model) — hr, spo2, nibpSystolic, nibpDiastolic,
        nibpMean, etco2, temp, rr. Any field that couldn't be read is None.
      confidences: {vital: 0-100} one entry per vital in VITALS (nibp's
        confidence covers its combined systolic/diastolic/mean read). When
        the ROI came from Tier-2, this is MIN(classifier_confidence,
        ocr_confidence) -- never an average -- per
        TIER2_RECOGNITION_SPIKE.md sec 07's "gate on the weakest signal"
        design; Tier-1 crops carry no classifier_confidence so this is
        unchanged from before M3 (== ocr_confidence exactly).

    crop_integrity (M5.4.1, optional, default None): a caller-owned,
    MUTATED-IN-PLACE dict, same pattern as app.validation.reconcile's
    `temporal_state` param. When provided, this function fills in
    {vital: bool} -- True means this tick's OCR evidence for that vital
    shows app.validation.crop_integrity.has_residual_content (the engine
    recognized characters beyond the parsed value, e.g. "8g" for a value of
    8 -- see that module for the measured root cause). When None (the
    default, and every pre-M5.4.1 call site), no extra diagnostics call is
    made and this function's cost/behaviour is byte-for-byte unchanged.

    diagnostics (M5.7.1, optional, default None): a caller-owned,
    MUTATED-IN-PLACE dict, same pattern as `crop_integrity` above. When
    provided, this function fills in {vital: OcrDiagnostics | None} -- the
    engine's raw/matched OCR text for that vital (None when the ROI itself
    resolved to no crop). Shares the SAME read_vital_with_diagnostics() call
    crop_integrity already makes when both are requested, so asking for both
    costs no extra OCR invocation over asking for either alone. Exists so a
    caller (Calibration's Verify step) can show an operator WHY a field read
    as empty -- "OCR saw X but couldn't parse a value" vs. "no crop at all"
    -- instead of just `-` with no explanation.

    skip_screen_detection (M5.7.1, optional, default False): when True,
    `roi_extractor` runs directly against `img`, bypassing detect_screen()
    entirely. Exists for CALIBRATED extractors only (app.pipeline.
    calibrated_roi.make_extractor / extract_rois_from_boxes) -- their
    NormalizedBox coordinates are established against the RAW frame the
    operator drew on (see calibrated_roi.py's module docstring: "a plain
    rescale... without needing any geometric correction"), not against
    whatever detect_screen() may or may not rectify a given frame to.
    detect_screen()'s quad search is inherently nondeterministic frame-to-
    frame on a real camera (glare/reflection/bezel-edge visibility) -- on a
    calibrated path, a frame where it happens to fire silently remaps every
    calibrated box onto a different pixel grid than the one it was
    normalized against, which is the root cause of "same setup, intermittent
    per-field OCR failure" (M5.7.1 investigation). False (the default)
    reproduces every pre-M5.7.1 call site's behaviour byte-for-byte -- the
    Tier-1/Tier-2 default ROI paths are unaffected by this parameter.
    """
    engine = engine or get_default_engine()
    roi_extractor = roi_extractor or get_default_roi_extractor()

    if skip_screen_detection:
        roi_source_image = img
    else:
        screen = detect_screen(img)
        roi_source_image = screen.image
    rois = roi_extractor(roi_source_image)

    reading: dict = {
        "hr": None,
        "spo2": None,
        "nibpSystolic": None,
        "nibpDiastolic": None,
        "nibpMean": None,
        "etco2": None,
        "temp": None,
        "rr": None,
    }
    confidences: Dict[str, float] = {}

    for vital in VITALS:
        roi_result = rois.get(vital)
        if roi_result is None:
            confidences[vital] = 0.0
            if crop_integrity is not None:
                crop_integrity[vital] = False
            if diagnostics is not None:
                diagnostics[vital] = None
            continue

        if crop_integrity is not None or diagnostics is not None:
            value, ocr_confidence, diag = engine.read_vital_with_diagnostics(roi_result.crop, vital)
            if crop_integrity is not None:
                crop_integrity[vital] = crop_is_suspicious(diag)
            if diagnostics is not None:
                diagnostics[vital] = diag
        else:
            value, ocr_confidence = engine.read_vital(roi_result.crop, vital)
        if roi_result.classifier_confidence is not None:
            # MIN, not average -- one weak signal (vision OR OCR) must not be
            # masked by a strong one. See TIER2_RECOGNITION_SPIKE.md sec 07.
            confidences[vital] = min(roi_result.classifier_confidence, ocr_confidence)
        else:
            confidences[vital] = ocr_confidence

        if vital == "nibp":
            if isinstance(value, NibpValue):
                reading["nibpSystolic"] = value.systolic
                reading["nibpDiastolic"] = value.diastolic
                reading["nibpMean"] = value.mean
        else:
            reading[vital] = value

    return reading, confidences
