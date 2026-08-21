"""M3 Phase 1: production-facing runtime wrapper around
backend/models/field_classifier.onnx (trained by
app/eval/tier2_field_classifier.py, see TIER2_M2_FIELD_CLASSIFIER_REPORT.md).

Mirrors app.pipeline.onnx_engine.OnnxDigitEngine's own pattern exactly (lazy
singleton, load-once ONNX session, labels.json + preprocess.json read from
disk, never hardcoded) -- this is the same inference abstraction the project
already has for a CNN-backed engine, not a second one.

Deliberately does NOT import anything from app.eval.* -- training/dataset-
construction code stays out of the runtime pipeline. The letterbox
preprocessing below is a fresh, independent implementation of the exact
convention documented in models/field_classifier.preprocess.json (and
originally implemented as app.eval.tier2_field_dataset._letterbox_gray) --
same reasoning app.pipeline.ocr.py gives for keeping its own preprocessing
independent from app.pipeline.segment.py's: different consumer, deliberately
un-shared so eval-only code never becomes a runtime import.

Never logs crop/image contents or patient data -- only shapes/counts/labels.
Never makes a network call.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")
DEFAULT_ONNX_PATH = os.path.join(_MODELS_DIR, "field_classifier.onnx")
DEFAULT_LABELS_PATH = os.path.join(_MODELS_DIR, "field_classifier.labels.json")
DEFAULT_PREPROCESS_PATH = os.path.join(_MODELS_DIR, "field_classifier.preprocess.json")

NOT_A_VITAL = "not_a_vital"


def model_available(onnx_path: str = DEFAULT_ONNX_PATH) -> bool:
    """Used by read_frame.py's ROI_ENGINE factory to decide whether 'tier2'
    is even a valid choice -- the model is never silently substituted for
    the Tier-1 colour path if it isn't present (same contract as
    onnx_engine.model_available)."""
    return os.path.isfile(onnx_path)


@dataclass
class FieldPrediction:
    """One candidate crop's FieldCNN result. confidence is 0-1 (raw softmax
    probability of the top class) -- callers that need the 0-100 scale the
    rest of the pipeline uses (OCR confidences, reconcile()'s tiers) convert
    explicitly, so this dataclass stays an honest reflection of what the
    model actually output."""

    label: str
    confidence: float
    probs: List[float]


def _letterbox_gray(crop: np.ndarray, size: int) -> np.ndarray:
    """Aspect-preserving resize onto a fixed size x size black canvas,
    centered. Must stay bit-for-bit equivalent to the training-time
    implementation (app.eval.tier2_field_dataset._letterbox_gray) or the
    model sees different input than it was trained/evaluated on -- the M2
    report's ONNX/native agreement number (100.0% prediction agreement) only
    holds if preprocessing matches exactly."""
    if crop.ndim == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    else:
        gray = crop
    h, w = gray.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((size, size), dtype=np.uint8)
    scale = min((size - 4) / h, (size - 4) / w)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size), dtype=np.uint8)
    y0 = (size - new_h) // 2
    x0 = (size - new_w) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


class FieldClassifierEngine:
    """Loads field_classifier.onnx once at construction. Deterministic:
    same crop in -> same prediction out, no randomness anywhere in the
    inference path (matches FieldCNN's own eval-mode/BatchNorm-frozen
    behaviour verified in M2 sec 15)."""

    def __init__(
        self,
        onnx_path: str = DEFAULT_ONNX_PATH,
        labels_path: str = DEFAULT_LABELS_PATH,
        preprocess_path: str = DEFAULT_PREPROCESS_PATH,
    ):
        if not os.path.isfile(onnx_path):
            raise FileNotFoundError(
                f"Tier-2 field classifier model not found at {onnx_path}. Train it first:\n"
                "  python -m app.eval.tier2_field_dataset\n"
                "  python -m app.eval.tier2_field_classifier\n"
                "or unset ROI_ENGINE / leave it at 'tesseract' to use the Tier-1 colour path."
            )
        if not os.path.isfile(labels_path):
            raise FileNotFoundError(f"Tier-2 field classifier labels file not found at {labels_path}.")
        if not os.path.isfile(preprocess_path):
            raise FileNotFoundError(f"Tier-2 field classifier preprocess file not found at {preprocess_path}.")

        with open(labels_path) as f:
            self.labels: List[str] = json.load(f)
        with open(preprocess_path) as f:
            self.preprocess: dict = json.load(f)

        try:
            self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        except Exception as exc:  # pragma: no cover - onnxruntime's own load failures
            raise RuntimeError(f"Failed to load Tier-2 field classifier ONNX model from {onnx_path}: {exc}") from exc

        self._input_name = self.preprocess.get("input_name", "crop")
        self._scale = self.preprocess.get("scale", 1.0 / 255.0)
        self._crop_size = self.preprocess.get("crop_size", 64)
        logger.info(
            "Tier-2 FieldCNN loaded: %d classes, crop_size=%d, input=%s", len(self.labels), self._crop_size, self._input_name
        )

    def classify(self, crops: List[np.ndarray]) -> List[FieldPrediction]:
        """One batched ONNX inference over every candidate crop at once --
        the reason a caller should collect all of a frame's candidates
        before calling this, rather than invoking it once per candidate.
        crops: RGB or grayscale uint8 ndarrays, any size (letterboxed here).
        Never raises on an empty/degenerate crop -- letterboxing degrades to
        an all-black canvas, which the model reads as (almost always)
        not_a_vital rather than crashing."""
        if not crops:
            return []
        letterboxed = np.stack([_letterbox_gray(c, self._crop_size) for c in crops])
        batch = (letterboxed.astype(np.float32) * self._scale)[:, None, :, :]  # (N, 1, H, W)
        (logits,) = self.session.run(None, {self._input_name: batch})
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = exp / exp.sum(axis=1, keepdims=True)
        top_idx = probs.argmax(axis=1)
        return [
            FieldPrediction(label=self.labels[i], confidence=float(probs[n, i]), probs=probs[n].tolist())
            for n, i in enumerate(top_idx)
        ]


_default_field_classifier: Optional[FieldClassifierEngine] = None


def get_default_field_classifier() -> FieldClassifierEngine:
    """Lazy singleton -- the ONNX session loads once per process, the first
    time ROI_ENGINE=tier2 actually reads a frame, not at import time and not
    once per candidate/frame. Mirrors read_frame.py's get_default_engine()
    singleton exactly."""
    global _default_field_classifier
    if _default_field_classifier is None:
        _default_field_classifier = FieldClassifierEngine()
    return _default_field_classifier


def _reset_default_field_classifier() -> None:
    """Test-only hook (mirrors the pattern tests/test_engine_selection.py
    already uses for _default_engine) -- not called by production code."""
    global _default_field_classifier
    _default_field_classifier = None
