import os
from typing import Dict, Optional, Tuple

import numpy as np

from app.pipeline.detect import detect_screen
from app.pipeline.ocr import NibpValue, OcrEngine, TesseractEngine
from app.pipeline.roi import extract_rois_by_colour

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

_default_engine: Optional[OcrEngine] = None


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


def read_frame(img: np.ndarray, engine: Optional[OcrEngine] = None) -> Tuple[dict, Dict[str, float]]:
    """Run the full S4 pipeline (detect -> rectify -> colour ROI) then OCR
    each vital. `engine` defaults to Tesseract but accepts anything
    implementing OcrEngine — this is the swap point for a future CNN (S11).

    Returns (reading, confidences):
      reading: VitalReading-shaped dict (camelCase keys matching the
        frontend/Pydantic model) — hr, spo2, nibpSystolic, nibpDiastolic,
        nibpMean, etco2, temp, rr. Any field that couldn't be read is None.
      confidences: {vital: 0-100} one entry per vital in VITALS (nibp's
        confidence covers its combined systolic/diastolic/mean read).
    """
    engine = engine or get_default_engine()

    screen = detect_screen(img)
    rois = extract_rois_by_colour(screen.image)

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
            continue

        value, confidence = engine.read_vital(roi_result.crop, vital)
        confidences[vital] = confidence

        if vital == "nibp":
            if isinstance(value, NibpValue):
                reading["nibpSystolic"] = value.systolic
                reading["nibpDiastolic"] = value.diastolic
                reading["nibpMean"] = value.mean
        else:
            reading[vital] = value

    return reading, confidences
