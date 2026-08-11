import os
import re
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

# Digits only ever appear as whole numbers on screen, except Temp which is
# always rendered with one decimal (see simulator/render/common.py's
# format_value) — so the whitelist/pattern differ only for "temp".
#
# PSM 8 ("single word"), not 7 ("single line"): empirically, PSM 7 on this
# monospace digit font systematically misreads a leading "7" as "1" (e.g.
# "74" -> "14", confirmed reproducible across multiple samples/crops) —
# apparently PSM 7's line-level segmentation heuristics get confused by this
# font's sans-serif "7" glyph. PSM 8 reads the exact same preprocessed image
# correctly. NIBP's split lines don't show this problem under PSM 6, so that
# config is left alone.
_DIGIT_CONFIG = "--psm 8 -c tessedit_char_whitelist=0123456789"
_DECIMAL_CONFIG = "--psm 8 -c tessedit_char_whitelist=0123456789."
_NIBP_CONFIG = "--psm 6 -c tessedit_char_whitelist=0123456789/"

_TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
]


@dataclass
class NibpValue:
    """NIBP's crop has two lines ("sys/dia" large, mean smaller beneath), so
    it can't collapse to one float like every other vital. Any field that
    couldn't be read stays None rather than being guessed."""

    systolic: Optional[float]
    diastolic: Optional[float]
    mean: Optional[float]


OcrValue = Union[float, NibpValue, None]


class OcrEngine(ABC):
    """Swappable digit-reading backend. read_frame.py and the eval harness
    only depend on this interface, never on Tesseract specifics — S11 can
    drop in a CNN-based engine later without touching either."""

    @abstractmethod
    def read_vital(self, crop: np.ndarray, vital_type: str) -> Tuple[OcrValue, float]:
        """crop: RGB uint8 ndarray — one vital's cropped region, as produced
        by app.pipeline.roi.extract_rois_by_colour.

        Returns (value, confidence): confidence is 0-100. value is a
        NibpValue when vital_type == "nibp", otherwise a plain float or None.
        Must never raise on empty/unreadable input — return (None, 0.0)
        (or an all-None NibpValue) instead of guessing.
        """
        raise NotImplementedError


def _locate_tesseract_binary(explicit_cmd: Optional[str]) -> Optional[str]:
    candidates: List[str] = []
    if explicit_cmd:
        candidates.append(explicit_cmd)
    env_cmd = os.environ.get("TESSERACT_CMD")
    if env_cmd:
        candidates.append(env_cmd)
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)
    candidates.extend(_TESSERACT_CANDIDATES)

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _preprocess(crop: np.ndarray, target_height: int = 120, pad: int = 20) -> Optional[np.ndarray]:
    """Grayscale -> upscale -> Otsu threshold (inverted so bright digits on a
    dark monitor background become black-on-white, which Tesseract expects)
    -> denoise -> quiet-zone padding."""
    if crop is None or crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
    h, w = gray.shape[:2]
    if h == 0 or w == 0:
        return None

    scale = max(1.0, target_height / h)
    resized = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_CUBIC)

    denoised = cv2.medianBlur(resized, 3) if min(resized.shape[:2]) >= 5 else resized

    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    # Otsu can end up inverted on a near-uniform crop (e.g. pure background,
    # no digit) — bias toward a mostly-white "quiet" image in that case.
    if np.mean(binary) < 127 and np.std(gray) < 5:
        binary = cv2.bitwise_not(binary)

    padded = cv2.copyMakeBorder(binary, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    return padded


def _split_text_lines(binary: np.ndarray, min_gap: int = 4, pad: int = 6) -> List[np.ndarray]:
    """Split a black-on-white binary image into separate horizontal text-line
    strips using its row-wise ink profile. NIBP's crop has two differently
    sized lines ("sys/dia" large, mean small below); asking Tesseract to read
    them as one block was observed to merge digits across lines (e.g.
    "120/78" + "92" -> "1206/78"), so each line is OCR'd separately instead."""
    has_ink = np.any(binary < 128, axis=1)
    lines: List[Tuple[int, int]] = []
    start = None
    gap = 0
    for y, ink in enumerate(has_ink):
        if ink:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                lines.append((start, y - gap))
                start = None
                gap = 0
    if start is not None:
        lines.append((start, len(has_ink) - 1))

    h = binary.shape[0]
    return [binary[max(0, s - pad) : min(h, e + pad + 1), :] for s, e in lines]


def _extract_tokens(data: dict) -> List[Tuple[str, float]]:
    tokens = []
    for text, conf_str in zip(data.get("text", []), data.get("conf", [])):
        text = text.strip()
        if not text:
            continue
        try:
            conf = float(conf_str)
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue
        tokens.append((text, conf))
    return tokens


def _joined_text_and_confidence(tokens: List[Tuple[str, float]], sep: str = "") -> Tuple[str, float]:
    if not tokens:
        return "", 0.0
    text = sep.join(t for t, _ in tokens)
    confidence = sum(c for _, c in tokens) / len(tokens)
    return text, confidence


class TesseractEngine(OcrEngine):
    """Tier-1 OCR backend using system Tesseract via pytesseract. Requires
    the Tesseract binary to be installed separately (see README) — pip only
    installs the Python wrapper."""

    def __init__(self, tesseract_cmd: Optional[str] = None):
        resolved = _locate_tesseract_binary(tesseract_cmd)
        if resolved:
            pytesseract.pytesseract.tesseract_cmd = resolved
        # If nothing was found, leave pytesseract's default ('tesseract') in
        # place — it will raise its own clear TesseractNotFoundError the
        # first time it's actually invoked, which is a better error than one
        # raised eagerly here for e.g. import-time construction in tests.

    def read_vital(self, crop: np.ndarray, vital_type: str) -> Tuple[OcrValue, float]:
        if vital_type == "nibp":
            return self._read_nibp(crop)
        if vital_type == "temp":
            return self._read_scalar(crop, _DECIMAL_CONFIG, decimal=True)
        return self._read_scalar(crop, _DIGIT_CONFIG, decimal=False)

    def _run_ocr_on_image(self, image: np.ndarray, config: str) -> dict:
        try:
            return pytesseract.image_to_data(image, config=config, output_type=Output.DICT)
        except pytesseract.TesseractError:
            return {"text": [], "conf": []}

    def _read_scalar(self, crop: np.ndarray, config: str, decimal: bool) -> Tuple[Optional[float], float]:
        processed = _preprocess(crop)
        if processed is None:
            return None, 0.0
        data = self._run_ocr_on_image(processed, config)
        tokens = _extract_tokens(data)
        text, confidence = _joined_text_and_confidence(tokens)
        if not text:
            return None, 0.0

        pattern = r"\d+\.\d+" if decimal else r"\d+"
        match = re.search(pattern, text)
        if not match:
            # Decimal point sometimes gets dropped/misread — fall back to
            # bare digits rather than failing the whole read.
            match = re.search(r"\d+", text)
            if not match:
                return None, 0.0

        try:
            value = float(match.group())
        except ValueError:
            return None, 0.0
        return value, confidence

    def _read_nibp(self, crop: np.ndarray) -> Tuple[NibpValue, float]:
        processed = _preprocess(crop)
        if processed is None:
            return NibpValue(None, None, None), 0.0

        # OCR-ing the whole two-line crop as one block was observed to merge
        # digits across the "sys/dia" and mean lines (e.g. "120/78" + "92"
        # misread as "1206/78"). Splitting into separate line strips first
        # and reading each independently fixed it in practice.
        lines = _split_text_lines(processed)
        did_split = len(lines) >= 2
        if not lines:
            lines = [processed]

        line_reads = []
        for line_img in lines:
            data = self._run_ocr_on_image(line_img, _NIBP_CONFIG)
            tokens = _extract_tokens(data)
            text, confidence = _joined_text_and_confidence(tokens, sep=" ")
            if text:
                line_reads.append((text, confidence, line_img.shape[0]))

        systolic = diastolic = mean = None
        confidences: List[float] = []

        # The "sys/dia" line is whichever contains "/"; if line-splitting
        # didn't actually separate anything, fall back to searching the
        # single joined block of text instead.
        sys_dia_line = next((t for t in line_reads if "/" in t[0]), None)
        if sys_dia_line is None and not did_split and line_reads:
            sys_dia_line = line_reads[0]

        if sys_dia_line:
            match = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", sys_dia_line[0])
            if match:
                systolic = float(match.group(1))
                diastolic = float(match.group(2))
                confidences.append(sys_dia_line[1])

        # The mean line is whatever's left — prefer the tallest non-sys/dia
        # line (mean is smaller font, but still the dominant remaining text).
        remaining = [t for t in line_reads if t is not sys_dia_line]
        for text, confidence, _height in remaining:
            match = re.search(r"\d{2,3}", text)
            if match:
                mean = float(match.group())
                confidences.append(confidence)
                break

        value = NibpValue(systolic=systolic, diastolic=diastolic, mean=mean)
        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return value, overall_confidence
