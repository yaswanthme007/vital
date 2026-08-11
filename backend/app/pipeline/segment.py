"""Digit-cell segmentation, shared between simulator/train/build_dataset.py
(labeling training cells against known ground truth) and
app/pipeline/onnx_engine.py (segmenting real inference crops) -- per the S11
task, the SAME function has to run both places ("no divergent logic"), so
whatever the model was trained on is exactly what it sees in production.

Deliberately independent from app.pipeline.ocr.py's private
_split_text_lines/_preprocess (Tesseract's own internal NIBP line-split) --
TesseractEngine is untouched this session, this is a fresh implementation
for the CNN path only.
"""

from typing import List, Optional, Tuple

import cv2
import numpy as np

# Vitals whose crop can contain two stacked lines (NIBP's "sys/dia" line
# plus a smaller mean line beneath). Every other vital's crop is one line.
TWO_LINE_VITALS = {"nibp"}

CELL_SIZE = 28  # fixed square CNN input side, in pixels


def binarize(crop: np.ndarray, target_height: int = 120) -> Optional[np.ndarray]:
    """Grayscale -> upscale -> Otsu threshold. Unlike ocr.py's _preprocess
    (which inverts for Tesseract's black-text-on-light-background
    expectation), ink stays WHITE (255) on a BLACK (0) background here --
    an arbitrary but consistent convention for the CNN path, matching how
    the crop actually looks (a bright glyph colour on a near-black monitor
    background needs no inversion)."""
    if crop is None or crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
    h, w = gray.shape[:2]
    if h == 0 or w == 0:
        return None

    scale = max(1.0, target_height / h)
    resized = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_CUBIC)
    denoised = cv2.medianBlur(resized, 3) if min(resized.shape[:2]) >= 5 else resized
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # Otsu can end up inverted on a near-uniform crop (pure background, no
    # digit) -- bias toward a mostly-black "empty" image in that case, same
    # safeguard as ocr.py's _preprocess uses for the opposite convention.
    if np.mean(binary) > 127 and np.std(gray) < 5:
        binary = cv2.bitwise_not(binary)
    return binary


def _ink_runs(has_ink: np.ndarray, min_gap: int) -> List[Tuple[int, int]]:
    """Index ranges of contiguous True runs in a 1D boolean array, merging
    runs separated by fewer than min_gap consecutive False entries."""
    runs: List[Tuple[int, int]] = []
    start = None
    gap = 0
    for i, ink in enumerate(has_ink):
        if ink:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                runs.append((start, i - gap))
                start = None
                gap = 0
    if start is not None:
        runs.append((start, len(has_ink) - 1))
    return runs


def split_lines(binary: np.ndarray, min_gap: int = 4, pad: int = 4) -> List[np.ndarray]:
    """Split a black-background binary image into horizontal line strips by
    row-wise ink runs -- NIBP's crop has two differently-sized lines."""
    has_ink = np.any(binary > 0, axis=1)
    runs = _ink_runs(has_ink, min_gap)
    h = binary.shape[0]
    return [binary[max(0, s - pad) : min(h, e + pad + 1), :] for s, e in runs]


def split_glyphs(line: np.ndarray, min_gap: int = 2, pad: int = 3) -> List[np.ndarray]:
    """Split one line strip into individual glyph cells by column-wise ink
    runs. The monospace font every layout renders vitals in means glyphs
    (including '/' and '.') are evenly spaced, so a simple ink-gap split is
    reliable up through moderate blur/noise -- it only breaks down when
    augmentation is heavy enough to visually merge adjacent glyphs, which
    build_dataset.py handles by discarding (not mislabeling) that sample."""
    has_ink = np.any(line > 0, axis=0)
    runs = _ink_runs(has_ink, min_gap)
    w = line.shape[1]
    return [line[:, max(0, s - pad) : min(w, e + pad + 1)] for s, e in runs]


def normalize_cell(cell: np.ndarray, size: int = CELL_SIZE) -> np.ndarray:
    """Resize a glyph crop (preserving aspect ratio) onto a fixed size x
    size black canvas, centered -- the CNN's actual input shape."""
    h, w = cell.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((size, size), dtype=np.uint8)

    scale = min((size - 4) / h, (size - 4) / w)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(cell, (new_w, new_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((size, size), dtype=np.uint8)
    y0 = (size - new_h) // 2
    x0 = (size - new_w) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def segment_digit_cells(crop: np.ndarray, vital_type: str) -> List[List[np.ndarray]]:
    """Segment one vital's crop into lines of individual glyph cells, each
    already normalized to CELL_SIZE x CELL_SIZE.

    Returns a list of lines (top to bottom), each a list of cells (left to
    right) -- for every vital except "nibp" this is always zero or one
    line. Never raises: an unreadable/empty/all-background crop returns [].
    """
    binary = binarize(crop)
    if binary is None:
        return []

    lines = split_lines(binary) if vital_type in TWO_LINE_VITALS else [binary]
    if not lines:
        return []

    result: List[List[np.ndarray]] = []
    for line in lines:
        glyphs = split_glyphs(line)
        if not glyphs:
            continue
        result.append([normalize_cell(g) for g in glyphs])
    return result
