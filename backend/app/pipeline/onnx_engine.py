"""Tier-2 OCR engine: a small CNN (trained by simulator/train/train_cnn.py,
exported to ONNX) run via onnxruntime. Implements the exact same OcrEngine
contract as TesseractEngine -- read_frame.py and the eval harness don't
know or care which engine they're calling.

Not the default -- see app/pipeline/read_frame.py's get_default_engine()
for the opt-in factory/env var. TesseractEngine stays Tier-1 and the
default in every case this file doesn't explicitly override.
"""

import json
import os
from typing import List, Optional, Tuple

import numpy as np
import onnxruntime as ort

from app.pipeline.ocr import NibpValue, OcrEngine, OcrValue
from app.pipeline.segment import segment_digit_cells

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")
DEFAULT_ONNX_PATH = os.path.join(_MODELS_DIR, "digit_cnn.onnx")
DEFAULT_LABELS_PATH = os.path.join(_MODELS_DIR, "digit_cnn.labels.json")
DEFAULT_PREPROCESS_PATH = os.path.join(_MODELS_DIR, "digit_cnn.preprocess.json")

BLANK_LABEL = "blank"

# Below this per-cell confidence (or a cell classified as the negative
# 'blank' class), the whole number is treated as unreadable rather than
# assembled from a guess -- mirrors TesseractEngine's "never guess" contract:
# one bad glyph fails the whole read, it isn't silently dropped.
MIN_READABLE_CONFIDENCE = 30.0


def model_available(onnx_path: str = DEFAULT_ONNX_PATH) -> bool:
    """Used by read_frame.py's engine factory to decide whether 'onnx' is
    even a valid choice -- the model is never silently substituted for
    Tesseract if it isn't present."""
    return os.path.isfile(onnx_path)


class OnnxDigitEngine(OcrEngine):
    def __init__(
        self,
        onnx_path: str = DEFAULT_ONNX_PATH,
        labels_path: str = DEFAULT_LABELS_PATH,
        preprocess_path: str = DEFAULT_PREPROCESS_PATH,
    ):
        if not os.path.isfile(onnx_path):
            raise FileNotFoundError(
                f"ONNX model not found at {onnx_path}. Train it first:\n"
                "  python -m simulator.train.measure_roi_jitter\n"
                "  python -m simulator.train.build_dataset\n"
                "  python -m simulator.train.train_cnn"
            )
        with open(labels_path) as f:
            self.labels: List[str] = json.load(f)
        with open(preprocess_path) as f:
            self.preprocess: dict = json.load(f)

        self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self._input_name = self.preprocess.get("input_name", "cell")
        self._scale = self.preprocess.get("scale", 1.0 / 255.0)

    def _classify_cells(self, cells: List[np.ndarray]) -> List[Tuple[str, float]]:
        """One batched ONNX inference over all of a line's cells at once.
        Returns (predicted_char, confidence 0-100) per cell, derived from
        the softmax of the top class."""
        if not cells:
            return []
        batch = (np.stack(cells).astype(np.float32) * self._scale)[:, None, :, :]  # (N, 1, H, W)
        (logits,) = self.session.run(None, {self._input_name: batch})
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = exp / exp.sum(axis=1, keepdims=True)
        top_idx = probs.argmax(axis=1)
        top_prob = probs[np.arange(len(cells)), top_idx]
        return [(self.labels[i], float(p) * 100.0) for i, p in zip(top_idx, top_prob)]

    def _assemble_line(self, cells: List[np.ndarray]) -> Tuple[Optional[str], float]:
        if not cells:
            return None, 0.0
        classified = self._classify_cells(cells)
        chars: List[str] = []
        confidences: List[float] = []
        for char, conf in classified:
            if char == BLANK_LABEL or conf < MIN_READABLE_CONFIDENCE:
                return None, min(c for _, c in classified)
            chars.append(char)
            confidences.append(conf)
        return "".join(chars), sum(confidences) / len(confidences)

    def read_vital(self, crop: np.ndarray, vital_type: str) -> Tuple[OcrValue, float]:
        lines = segment_digit_cells(crop, vital_type)
        if not lines:
            return (NibpValue(None, None, None) if vital_type == "nibp" else None), 0.0

        if vital_type == "nibp":
            return self._read_nibp(lines)

        text, confidence = self._assemble_line(lines[0])
        if text is None:
            return None, confidence
        try:
            return float(text), confidence
        except ValueError:
            return None, 0.0

    def _read_nibp(self, lines: List[List[np.ndarray]]) -> Tuple[NibpValue, float]:
        systolic = diastolic = mean = None
        confidences: List[float] = []

        sys_dia_line = None
        mean_line = None
        for cells in lines:
            text, confidence = self._assemble_line(cells)
            if text is None:
                continue
            if "/" in text and sys_dia_line is None:
                sys_dia_line = (text, confidence)
            elif mean_line is None:
                mean_line = (text, confidence)

        if sys_dia_line:
            parts = sys_dia_line[0].split("/")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                systolic = float(parts[0])
                diastolic = float(parts[1])
                confidences.append(sys_dia_line[1])

        if mean_line and mean_line[0].isdigit():
            mean = float(mean_line[0])
            confidences.append(mean_line[1])

        value = NibpValue(systolic=systolic, diastolic=diastolic, mean=mean)
        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return value, overall_confidence
