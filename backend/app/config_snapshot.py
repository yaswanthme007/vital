"""M5.6: the frozen production configuration, reported by the running
process rather than asserted by a document.

WHY A MODULE AND NOT JUST A MARKDOWN TABLE. Every milestone in the M5
series had to open several files and read constants by hand to state "the
exact configuration that was validated" (see e.g.
docs/M5_5_FINAL_VALIDATION_REPORT.md sec 2, assembled that way). That is
transcription, and transcription drifts. This module reads the SAME
constants the pipeline actually imports at runtime, so a snapshot taken
from a live process cannot disagree with what that process is doing --
which is the only form of "frozen configuration" worth having for
reproducibility.

READ-ONLY BY CONSTRUCTION. Nothing here sets an environment variable,
mutates a constant, or influences any decision the pipeline makes. It
imports and reports. Deleting this module would not change a single
pipeline behaviour.

The M5.6 report's "exact final configuration" section, and
docs/M5_6_FROZEN_CONFIG.json, are both generated from snapshot().
"""

import os
import platform
import subprocess
from typing import Any, Dict, Optional

# Feature-flag defaults, kept as literals matching the code that reads them
# (app.pipeline.read_frame, app.ws.vitals) so a drift between "documented
# default" and "actual default" is a visible test failure, not a surprise on
# stage. Each of these is asserted against its real reader in
# tests/test_m5_6_promotion.py.
ROI_ENGINE_DEFAULT = "tesseract"
OCR_ENGINE_DEFAULT = "tesseract"
LAYOUT_TRACKING_DEFAULT = "auto"
TEMPORAL_CORROBORATION_DEFAULT = "off"


def _tesseract_version() -> Optional[str]:
    """Queried, never assumed -- M5.5 sec 2 made the same point about the
    binary actually resolved at runtime. Returns None rather than raising if
    the binary is missing, so a snapshot still renders on a machine without
    Tesseract installed."""
    try:
        from app.pipeline.ocr import _locate_tesseract_binary

        binary = _locate_tesseract_binary(None)
        if not binary:
            return None
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        lines = (out.stdout or out.stderr).strip().splitlines()
        return lines[0].strip() if lines else None
    except Exception:
        return None


def _dependency_versions() -> Dict[str, Optional[str]]:
    from importlib.metadata import PackageNotFoundError, version

    names = [
        "fastapi", "uvicorn", "pydantic", "sqlalchemy", "pytest", "httpx",
        "Pillow", "opencv-python-headless", "numpy", "pytesseract",
        "torch", "onnx", "onnxruntime",
    ]
    out: Dict[str, Optional[str]] = {}
    for name in names:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def _model_artifacts() -> Dict[str, Any]:
    """The ONNX artifacts on disk are INERT in the shipped configuration --
    docs/ARCHITECTURE.md's retirement rationale, restated by M5.5 sec 2 and
    listed as remaining risk 4 there (an operator setting OCR_ENGINE=onnx or
    ROI_ENGINE=tier2 would silently reintroduce the retired FieldCNN). The
    snapshot reports both facts: present-on-disk, and whether the current
    environment would actually load them.
    """
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models_dir = os.path.join(backend_dir, "models")

    def _present(name: str) -> bool:
        return os.path.isfile(os.path.join(models_dir, name))

    roi_engine = os.environ.get("ROI_ENGINE", ROI_ENGINE_DEFAULT).strip().lower()
    ocr_engine = os.environ.get("OCR_ENGINE", OCR_ENGINE_DEFAULT).strip().lower()
    return {
        "fieldClassifierOnnxPresent": _present("field_classifier.onnx"),
        "digitCnnOnnxPresent": _present("digit_cnn.onnx"),
        "fieldClassifierLoaded": roi_engine == "tier2",
        "digitCnnLoaded": ocr_engine == "onnx",
        "note": (
            "Both artifacts are inert in the shipped configuration: the calibrated ROI path "
            "uses no classifier (an operator draws the boxes) and OCR_ENGINE=tesseract never "
            "touches the digit CNN. See docs/ARCHITECTURE.md."
        ),
    }


def snapshot() -> Dict[str, Any]:
    """The complete resolved configuration of THIS process."""
    from app.alerts.rules import THROTTLE_WINDOW_MS
    from app.pipeline import calibrated_roi, layout_tracker, ocr
    from app.validation import rules, temporal

    roi_engine = os.environ.get("ROI_ENGINE", ROI_ENGINE_DEFAULT).strip().lower()
    ocr_engine = os.environ.get("OCR_ENGINE", OCR_ENGINE_DEFAULT).strip().lower()
    layout_tracking = os.environ.get("LAYOUT_TRACKING", LAYOUT_TRACKING_DEFAULT).strip().lower()
    temporal_corroboration = os.environ.get(
        "TEMPORAL_CORROBORATION", TEMPORAL_CORROBORATION_DEFAULT
    ).strip().lower()

    roi_scope = (
        "NON-CAMERA paths only (ReplaySource('pipeline'), eval scripts, and the fallback "
        "when no calibration profile exists). The live camera WebSocket binds the "
        "database's active CalibrationProfile directly and never reads this flag; "
        "POST /api/pipeline/read-frame prefers that same profile since M5.6."
    )
    temporal_note = (
        "Must remain OFF in production. M5.4 found a confidently-wrong regression this "
        "mechanism can produce; M5.4.1 closed that specific hole with a crop-integrity "
        "gate but measured zero net accuracy benefit, so M5.5 recommended against "
        "enabling it and M5.6 does not enable it. The implementation, its tests and its "
        "eval harness stay in the tree as an experimental/research feature."
    )

    return {
        "milestone": "M5.6",
        "featureFlags": {
            "ROI_ENGINE": {
                "value": roi_engine,
                "default": ROI_ENGINE_DEFAULT,
                "explicitlySet": "ROI_ENGINE" in os.environ,
                "scope": roi_scope,
            },
            "OCR_ENGINE": {
                "value": ocr_engine,
                "default": OCR_ENGINE_DEFAULT,
                "explicitlySet": "OCR_ENGINE" in os.environ,
            },
            "LAYOUT_TRACKING": {
                "value": layout_tracking,
                "default": LAYOUT_TRACKING_DEFAULT,
                "explicitlySet": "LAYOUT_TRACKING" in os.environ,
                "enabled": layout_tracking != "off",
            },
            "TEMPORAL_CORROBORATION": {
                "value": temporal_corroboration,
                "default": TEMPORAL_CORROBORATION_DEFAULT,
                "explicitlySet": "TEMPORAL_CORROBORATION" in os.environ,
                "enabled": temporal_corroboration == "on",
                "note": temporal_note,
            },
        },
        "ocr": {
            # M5.8: the config production actually reads with. Every scalar
            # vital routes here now; NIBP reads each of its ink row strips
            # with it too (falling back to nibpConfig per strip).
            "sparseConfig": ocr._SPARSE_CONFIG,
            "dominantRowHeightRatio": ocr.DOMINANT_ROW_HEIGHT_RATIO,
            "dominantRowOverlapMin": ocr.DOMINANT_ROW_OVERLAP_MIN,
            "quietZonePad": ocr._QUIET_ZONE_PAD,
            # The M4-era per-vital routing. Retained and reported because
            # the M4.5/M4.6/M5.1 eval scripts reproduce it and
            # digitPsm10Config is still production's guarded
            # single-character fallback -- but sparseConfig above is what a
            # scalar read starts from. See ocr.py's M5.8 comment block.
            "digitConfig": ocr._DIGIT_CONFIG,
            "digitPsm10Config": ocr._DIGIT_PSM10_CONFIG,
            "psm10Vitals": sorted(ocr._PSM10_VITALS),
            "nibpConfig": ocr._NIBP_CONFIG,
            "etco2Config": ocr._ETCO2_CONFIG,
            "charWhitelist": None,
            "whitelistNote": (
                "M5.1 removed tessedit_char_whitelist everywhere; see "
                "docs/M5_1_OCR_CONFIDENCE_REPORT.md."
            ),
            "tesseractVersion": _tesseract_version(),
        },
        "calibration": {
            "widthSafetyPadFraction": calibrated_roi.WIDTH_SAFETY_PAD_FRACTION,
            "maxAspectRatioDrift": calibrated_roi.MAX_ASPECT_RATIO_DRIFT,
            "schema": "app.models.calibration.CalibrationProfile",
        },
        "tracking": {
            "minInliers": layout_tracker.MIN_INLIERS,
            "minRawMatches": layout_tracker.MIN_RAW_MATCHES,
            "maxReprojectionErrorPx": layout_tracker.MAX_REPROJECTION_ERROR_PX,
            "minScale": layout_tracker.MIN_SCALE,
            "maxScale": layout_tracker.MAX_SCALE,
            "maxRotationDeg": layout_tracker.MAX_ROTATION_DEG,
            "maxTranslationDiagonals": layout_tracker.MAX_TRANSLATION_DIAGONALS,
            "trackMaxDim": layout_tracker.TRACK_MAX_DIM,
            "orbFeatures": layout_tracker.ORB_FEATURES,
        },
        "confidence": {
            "confidenceHighMin": rules.CONFIDENCE_HIGH_MIN,
            "confidenceMediumMin": rules.CONFIDENCE_MEDIUM_MIN,
            "rangeBounds": {k: list(v) for k, v in rules.RANGE_BOUNDS.items()},
            "fahrenheitBounds": list(rules.FAHRENHEIT_BOUNDS),
            "jumpLimits": {k: list(v) for k, v in rules.JUMP_LIMITS.items()},
        },
        "temporal": {
            "confidenceTemporalFloor": temporal.CONFIDENCE_TEMPORAL_FLOOR,
            "temporalAgreementMinRun": temporal.TEMPORAL_AGREEMENT_MIN_RUN,
            "activeInThisProcess": temporal_corroboration == "on",
        },
        "alerts": {"throttleWindowMs": THROTTLE_WINDOW_MS},
        "models": _model_artifacts(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": _dependency_versions(),
        },
        "validationClaim": (
            "VITAL is validated on the available real-world monitor video datasets and the "
            "real application pipeline. It is a technical demonstration/prototype: not "
            "clinically validated, not medically certified, not approved for patient care, "
            "and not established as diagnostically accurate."
        ),
    }
