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
# format_value) — so the pattern used to PARSE the recognized text still
# differs only for "temp" (see _read_scalar's decimal flag). The character
# WHITELIST that used to differ the same way is gone -- see M5.1 below.
#
# PSM 8 ("single word"), not 7 ("single line"): empirically, PSM 7 on this
# monospace digit font systematically misreads a leading "7" as "1" (e.g.
# "74" -> "14", confirmed reproducible across multiple samples/crops) —
# apparently PSM 7's line-level segmentation heuristics get confused by this
# font's sans-serif "7" glyph. PSM 8 reads the exact same preprocessed image
# correctly. NIBP's split lines don't show this problem under PSM 6, so that
# config is left alone.
#
# M5.1: `tessedit_char_whitelist` removed from hr/temp's config (root-caused
# in docs/M5_1_OCR_CONFIDENCE_REPORT.md, same mechanism M4.4 below already
# fixed for NIBP/EtCO2 and M4.6 deferred for HR/SpO2/RR/Temp). Isolated,
# controlled A/B on identical oracle crops (whitelist present vs absent,
# PSM/preprocessing held fixed) via app/eval/m5_1_ocr_config_sweep.py:
# whitelisted HR reads a genuinely correct "85" at confidence 0 on Dataset B
# sample_0009 -- SAME text, SAME crop -- confidence 33 with the whitelist
# gone. No Dataset A field regresses (52/52 -> 52/52 correct for hr; overall
# oracle accuracy 90.6% -> 98.7%, driven mostly by RR, see below); zero
# newly-introduced confidently-wrong reconcile() confirmations on either
# dataset. Parsing safety is unaffected: the regex-based digit/decimal
# extraction downstream already ignores anything Tesseract emits that isn't
# a digit run, exactly as M4.4's NIBP/EtCO2 fix already established.
_DIGIT_CONFIG = "--psm 8"
_DECIMAL_CONFIG = "--psm 8"

# M4.6 (promoted from the eval-only NarrowSelectivePsmEngine evaluated in
# TIER2_M4_5... / M4_5_SELECTIVE_PSM_RELIABILITY_REPORT.md): SpO2 and RR
# read more reliably under PSM 10 ("single character") than PSM 8 on this
# dataset -- confirmed accuracy 11.1%->33.3% (SpO2) and 4.7%->62.8% (RR),
# with HR/NIBP/EtCO2/Temp byte-for-byte unaffected and the backend suite
# still green. HR is deliberately EXCLUDED from this routing: M4.3 §12/§14
# found routing HR through psm10 hurt its confirmed-state reliability, and
# M4.5's own narrower routing (which this promotes verbatim) never included
# it. See M4_5_SELECTIVE_PSM_RELIABILITY_REPORT.md and
# M4_6_PRODUCTION_PROMOTION_REPORT.md for the full evidence trail.
#
# M5.1: whitelist removed here too, same fix/evidence as _DIGIT_CONFIG
# above -- this was the worst single instance found: production SpO2 on
# Dataset B read 82% of frames correctly at confidence exactly 0 (mean
# correct-read confidence 0.1 whitelisted vs 61.4 not, oracle crops,
# identical PSM/preprocessing). This also resolves RR's confidence-
# calibration issue carried forward unfixed since M4.6 §10 ("most wrong RR
# reads arrive at >=90% confidence") -- on Dataset A oracle crops RR's own
# OCR accuracy rises 53.5%->97.7% with the whitelist gone (wrong reads were
# often the whitelist forcing a false digit onto a non-digit token; without
# it more of those correctly come back "no reading" instead of a wrong one).
# See docs/M5_1_OCR_CONFIDENCE_REPORT.md.
_DIGIT_PSM10_CONFIG = "--psm 10"
_PSM10_VITALS = {"spo2", "rr"}  # HR explicitly excluded -- see comment above

# NIBP and EtCO2 (M4.4, root-caused in TIER2_M4_3_RELIABILITY_REPORT.md §8
# and re-verified directly against this Tesseract install before this
# change): `tessedit_char_whitelist` makes THIS Tesseract build's reported
# per-token confidence collapse toward 0 on these two crops' longer/more
# structured OCR reads, even when the recognized text is correct --
# confirmed by re-running the identical crop with and without the
# whitelist: e.g. NIBP's "150/80" line goes from confidence 0 (whitelisted)
# to 85 (not) for the SAME correct text, on every one of the 14 NIBP crops
# this dataset provides GT for, with zero change to the recognized digits
# (the whitelist was never adding parsing safety here -- the regex-based
# digit/slash extraction downstream already ignores whatever else Tesseract
# recognizes). This is a confidence-extraction fix only: it does not touch
# _DIGIT_CONFIG, so hr/spo2/rr (PSM promotion explicitly deferred past M4.4,
# see TIER2_M4_4_RULES_LAYER_REPORT.md §2) are byte-for-byte unaffected.
_NIBP_CONFIG = "--psm 6"
_ETCO2_CONFIG = "--psm 8"

# ============================================================================
# M5.8: THE DOMINANT-ROW READER -- the production scalar/NIBP path
# ============================================================================
#
# ROOT CAUSE it fixes (measured directly against the demo laptop's own real
# camera frames -- the nine 1280x720 calibration reference frames stored in
# the live vital.db, each a photograph of the physical anaesthesia monitor;
# see docs/M5_8_REAL_CAMERA_OBSERVATION.md and
# app/eval/m5_8_dominant_row_eval.py):
#
# An operator-drawn ROI around a monitor's primary reading ALWAYS also
# contains that field's small alarm-limit labels, because the monitor draws
# them inside the field's own display slot -- EtCO2's box contains "65"/"25"
# (the alarm limits) and "inCO2 4" as well as the large "34"; RR's contains
# "30"/"8" as well as "12"; HR's contains "130"/"50" as well as "88"; SpO2's
# contains "100"/"92". No amount of careful box-drawing removes them: they
# sit between the waveform and the digits, inside the slot.
#
# Every PSM this file previously routed to (8 "single word", 10 "single
# character", 6 "uniform block") returns those labels as part of ONE
# recognized text blob, which _joined_text_and_confidence then concatenates
# with no separator and _read_scalar's `re.search(r"\d+")` mines for the
# FIRST digit run. So the value reported was frequently a label, or a
# label spliced onto the real value:
#   EtCO2 "65 25 34 4"      -> "234"  (and, on other frames, "4" -- the
#                                      literal 4 mmHg the demo recorded)
#   RR    "30 8 12"         -> "42" / "420"
#   HR    "130 50 88"       -> "9" / "3" / "94"
#   Temp  "101.0 79.0 98.6" -> "986" / "8.6"
# This is the single root cause behind every "the ledger recorded a value
# that was never on the monitor" case in the demo recording.
#
# THE FIX, in one sentence: ask Tesseract for SPARSE text (--psm 11, which
# returns each visually separate text fragment as its own token WITH its own
# bounding box), then keep only the tokens belonging to the TALLEST
# digit-bearing row and parse the value from those alone.
#
# Why glyph height is the right discriminator, and not a heuristic reach:
# a patient monitor renders its primary reading several times larger than
# the alarm limits/scale labels drawn beside it -- that size difference IS
# the monitor's own visual encoding of "this is the number, those are its
# limits". Measured on the real frames, the primary digits run 75-160px
# tall after _preprocess while every label token is 16-40px. Selecting on
# geometry is also the safe kind of selection: it never looks at what the
# digits SAY, so it cannot prefer a "more plausible" reading -- which is
# exactly the property "do not simply pick whichever result looks
# plausible" requires.
#
# Measured effect on the real camera frames (53 GT-scored fields, the same
# operator-drawn boxes + 20% width pad production uses):
#   before: 54.7% correct, 32.1% WRONG, 13.2% no-read
#   after : 75.5% correct,  7.5% WRONG, 17.0% no-read
# The WRONG rate -- the safety-critical number -- drops 4x, and the extra
# no-reads are free on a live 1 Hz stream (a no-read holds for one tick;
# the next frame reads again). Correct reads also arrive at far higher
# Tesseract confidence (typically 90-96 instead of 8-55) because the token
# being scored is now the value alone rather than a blob of unrelated text,
# which is what makes the downstream confidence gate meaningful again.
_SPARSE_CONFIG = "--psm 11"

# A token counts as part of the dominant row when it is at least this
# fraction of the tallest digit-bearing token's height AND overlaps it
# vertically by at least DOMINANT_ROW_OVERLAP_MIN of the shorter token's
# height. 0.6 comfortably admits a value split into two tokens by
# Tesseract (e.g. "9" + "8", or a decimal point splitting "98" from "6")
# while excluding every label token measured on the real frames (labels sit
# at 0.1-0.35 of the primary height). The overlap term is what stops a
# same-size number on a DIFFERENT row (e.g. the Pulse field's own big
# digits, if a box is drawn loosely) from being spliced onto the value.
DOMINANT_ROW_HEIGHT_RATIO = 0.6
DOMINANT_ROW_OVERLAP_MIN = 0.5

# Quiet-zone border _preprocess adds around a crop before OCR. PSM 11's
# layout analysis needs real whitespace around the text to segment it at
# all: measured on Dataset A's tight annotated crops (glyphs filling ~95%
# of the crop), the 20px border this file used before M5.8 produced ZERO
# tokens for those crops, while a wider border produced the correct
# "86"/"98"/"97" at confidence 92-96 on the very same pixels.
#
# 30 is a swept value, not a guess (app/eval/m5_8_dominant_row_eval.py
# --sweep-pad, over three independent bodies of evidence):
#   pad | simulator (15 fields) | Dataset B (49) | real camera frames (53)
#    20 |  2 correct, 12 no-read|  9c 0w 40n     |  (not run -- sim broken)
#    30 | 15 correct,  0 wrong  |  9c 0w 40n     |  41c 5w 7n
#    50 | 14 correct,  1 wrong  | 11c 0w 38n     |  41c 5w 7n
#    80 | 14 correct,  1 wrong  | 11c 0w 38n     |  41c 5w 7n
# The real-camera frames -- the evidence that actually represents the
# product's input -- are completely insensitive across 30-80, so the choice
# is made on the other two: 30 reads every synthetic simulator field
# correctly (50+ misreads EtCO2 38 as 33 at confidence 66) at the cost of 2
# reads on Dataset B's known-degraded arm. Fewer wrong reads wins, per this
# project's stated posture.
_QUIET_ZONE_PAD = 30

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


@dataclass(frozen=True)
class OcrDiagnostics:
    """M5.4.1: extra, OPTIONAL per-read evidence beyond (value, confidence)
    -- exists so a caller that wants crop-integrity evidence
    (app.validation.crop_integrity) can get it WITHOUT changing
    OcrEngine.read_vital's contract, which every existing caller (Tier-2's
    OnnxDigitEngine included, and every 2-tuple-unpacking call site across
    read_frame.py/the eval scripts/tests) keeps using byte-for-byte
    unmodified. See read_vital_with_diagnostics below.

    raw_text: the engine's full recognized text for this crop, BEFORE the
      digit-extracting regex trims it down to a value (e.g. Tesseract may
      recognize "8g" when a second digit was partially clipped out of the
      crop; the regex then parses only "8" as the value, discarding the "g"
      — raw_text is what lets a caller notice that discard happened).
    matched_text: the exact substring raw_text's regex match consumed to
      produce the returned value (e.g. "8"). None when no value was parsed.
    incomplete_row (M5.8): True when the crop contains full-height ink the
      recognized tokens do NOT account for -- i.e. the engine read part of
      the primary reading and silently dropped the rest. This is the
      GEOMETRIC counterpart to raw_text/matched_text's textual residual
      signal, and it catches a failure the textual one cannot: measured on
      Dataset A sample_0010, PSM 11 tokenizes a clearly-rendered "34" as
      just "4" at 96% confidence, with nothing left over in the text to
      compare -- a confidently-wrong truncation that looks perfectly clean
      to has_residual_content. See _dominant_row_is_complete. Engines that
      cannot derive this (the OcrEngine base default, S11's OnnxDigitEngine)
      leave it False, which app.validation.crop_integrity treats as
      "nothing to flag", never as "suspicious" -- the same posture the
      empty-raw_text default already takes.
    """

    raw_text: str = ""
    matched_text: Optional[str] = None
    incomplete_row: bool = False


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

    def read_vital_with_diagnostics(
        self, crop: np.ndarray, vital_type: str, variant: str = "otsu"
    ) -> Tuple[OcrValue, float, OcrDiagnostics]:
        """M5.4.1: same read as read_vital, plus OcrDiagnostics for a caller
        that wants crop-integrity evidence (app.pipeline.read_frame's
        `crop_integrity` param). NOT abstract: engines that cannot produce
        this evidence (this base default, used by S11's OnnxDigitEngine)
        just report an empty OcrDiagnostics — which app.validation.
        crop_integrity.has_residual_content treats as "nothing to flag",
        never as "suspicious". A caller that doesn't know how to derive
        crop-integrity evidence for an engine must never guess in either
        direction. TesseractEngine overrides this with the real evidence
        (below); read_vital's own behaviour is unchanged by this override
        existing (it delegates from the same shared implementation).

        variant (M5.7.2): forwarded to _preprocess for engines that support
        it (TesseractEngine does; this base default ignores it, matching
        base read_vital's own no-op stance on anything engine-specific)."""
        value, confidence = self.read_vital(crop, vital_type)
        return value, confidence, OcrDiagnostics()


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


# M5.7.2: the second thresholding strategy _preprocess can use, selected via
# its `variant` param. 'otsu' (the default/original, below) picks a single
# global split point for the whole crop -- exactly right for a clean, evenly
# lit crop, but a global threshold is the first thing to misbehave under
# per-pixel-varying degradation (glare hot-spots, uneven exposure, JPEG
# blockiness) since one split point can no longer cleanly separate glyph from
# background across the whole image. 'adaptive' instead (a) CLAHE-normalizes
# local contrast first, then (b) thresholds each local neighbourhood against
# its OWN nearby mean rather than one global value -- exactly the "contrast
# normalization" + "adaptive thresholding" pair a burst-verification fallback
# needs for a frame Otsu alone can't stabilize. It is NEVER the default: see
# app.pipeline.burst_verify for where/why it's invoked (only as a second
# opinion on fields the primary otsu-preprocessed burst couldn't stabilize),
# and app/eval/m5_7_2_burst_verification_eval.py for the measured evidence
# this doesn't regress the existing Otsu path when used that way.
_ADAPTIVE_BLOCK_SIZE = 25  # odd, ~1/5 of target_height -- must exceed stroke width, not exceed glyph height
_ADAPTIVE_C = 10


def _preprocess(
    crop: np.ndarray, target_height: int = 120, pad: int = 20, variant: str = "otsu"
) -> Optional[np.ndarray]:
    """Grayscale -> upscale -> denoise -> threshold -> quiet-zone padding.
    Output is always black glyph strokes on a white background, which
    Tesseract expects — but which side of the split IS the glyph isn't
    knowable in advance: a dark monitor screen has bright digits on black
    (the historical assumption here), while a light dashboard has coloured
    digits on white, and grayscale-brightness order between "digit" and
    "background" flips between those two cases. Forcing THRESH_BINARY_INV
    unconditionally (the old behaviour) only produced correct output for the
    dark-background case; a light-background source got the opposite
    polarity, which Tesseract reads far less reliably. Both thresholding
    variants below still cleanly separate the two clusters either way, so the
    fix is to threshold without forcing a direction, then invert based on
    which cluster is the majority — glyph strokes are always a small minority
    of a crop's area relative to its background, regardless of which one is
    visually darker.

    variant: 'otsu' (default, byte-for-byte the original M1-era behaviour —
    every existing call site that doesn't pass this parameter is completely
    unaffected) or 'adaptive' (M5.7.2, see _ADAPTIVE_BLOCK_SIZE/_ADAPTIVE_C
    above)."""
    if crop is None or crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
    h, w = gray.shape[:2]
    if h == 0 or w == 0:
        return None

    scale = max(1.0, target_height / h)
    resized = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_CUBIC)

    denoised = cv2.medianBlur(resized, 3) if min(resized.shape[:2]) >= 5 else resized

    if variant == "adaptive":
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        normalized = clahe.apply(denoised)
        binary = cv2.adaptiveThreshold(
            normalized, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            _ADAPTIVE_BLOCK_SIZE, _ADAPTIVE_C,
        )
    else:
        _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    white_count = int(np.count_nonzero(binary == 255))
    black_count = binary.size - white_count
    if black_count > white_count:
        binary = cv2.bitwise_not(binary)

    # Can end up inverted on a near-uniform crop (e.g. pure background, no
    # digit) — bias toward a mostly-white "quiet" image in that case.
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


@dataclass(frozen=True)
class _Token:
    """One Tesseract token WITH its bounding box, in the preprocessed
    image's own pixel space. _extract_tokens (above) deliberately keeps its
    original (text, confidence) shape -- app/eval/m4_ocr_benchmark.py and
    the M4-era eval engines still consume it -- so this is an addition, not
    a replacement."""

    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int

    @property
    def bottom(self) -> int:
        return self.top + self.height


def _extract_tokens_with_boxes(data: dict) -> List[_Token]:
    """Same filtering rules as _extract_tokens (blank text and negative
    confidence dropped -- Tesseract emits conf=-1 for layout rows that
    carry no recognized text), plus the geometry the dominant-row selection
    needs."""
    tokens: List[_Token] = []
    fields = ("text", "conf", "left", "top", "width", "height")
    if not all(f in data for f in fields):
        return tokens
    for text, conf_str, left, top, width, height in zip(*(data[f] for f in fields)):
        text = text.strip()
        if not text:
            continue
        try:
            conf = float(conf_str)
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue
        tokens.append(
            _Token(text=text, confidence=conf, left=int(left), top=int(top), width=int(width), height=int(height))
        )
    return tokens


def _vertical_overlap_fraction(a: _Token, b: _Token) -> float:
    """Shared vertical extent as a fraction of the SHORTER token's height --
    so a tall value digit and a short decimal-point fragment sitting on the
    same baseline still count as one row, while a full-height token on a
    different row does not."""
    overlap = min(a.bottom, b.bottom) - max(a.top, b.top)
    shorter = max(1, min(a.height, b.height))
    return max(0, overlap) / shorter


def dominant_row_tokens(tokens: List[_Token]) -> List[_Token]:
    """The tokens making up a crop's PRIMARY (largest) reading, left to
    right -- see the _SPARSE_CONFIG comment block for the root cause this
    exists to fix and the measured evidence behind the two constants.

    The anchor is the tallest token that contains at least one digit (a
    tall non-digit fragment -- a waveform stroke Tesseract labelled "|", a
    unit label -- must never define the row). Falling back to the tallest
    token of any kind when NO token contains a digit keeps the caller's
    diagnostics honest: it still reports what was recognized, and the
    value-parsing regex upstream will simply find no digits and return
    None, which is the correct "unreadable" outcome."""
    if not tokens:
        return []
    digit_tokens = [t for t in tokens if any(ch.isdigit() for ch in t.text)]
    anchor = max(digit_tokens or tokens, key=lambda t: t.height)
    selected = [
        t
        for t in tokens
        if t.height >= DOMINANT_ROW_HEIGHT_RATIO * anchor.height
        and _vertical_overlap_fraction(t, anchor) >= DOMINANT_ROW_OVERLAP_MIN
    ]
    return sorted(selected, key=lambda t: t.left)


def _group_rows(tokens: List[_Token]) -> List[List[_Token]]:
    """Groups tokens into visual rows by vertical overlap, topmost row
    first, each row ordered left to right. Used by the NIBP read, whose
    crop legitimately holds TWO readings the operator wants (the "sys/dia"
    line and the mean beneath it) rather than one value plus labels."""
    rows: List[List[_Token]] = []
    for token in sorted(tokens, key=lambda t: t.top):
        for row in rows:
            if _vertical_overlap_fraction(token, row[0]) >= DOMINANT_ROW_OVERLAP_MIN:
                row.append(token)
                break
        else:
            rows.append([token])
    return [sorted(row, key=lambda t: t.left) for row in rows]


def _row_text_and_confidence(tokens: List[_Token], sep: str = "") -> Tuple[str, float]:
    return _joined_text_and_confidence([(t.text, t.confidence) for t in tokens], sep=sep)


# A single-character read is only believed when its own bounding box covers
# at least this fraction of the crop's total horizontal ink extent -- see
# _covers_the_ink below.
_INK_COVERAGE_MIN = 0.8

# How far outside the recognized tokens' own horizontal span a full-height
# ink component may sit before _dominant_row_is_complete calls the read
# incomplete, as a fraction of the dominant glyph height. A recognized
# glyph's bounding box does not always tightly enclose its own antialiased
# edge pixels, so a couple of stray columns are expected; a whole dropped
# digit is not (a digit is ~0.5-0.7 of its own height wide).
_UNCOVERED_INK_TOLERANCE = 0.25


def _dominant_row_is_complete(processed: np.ndarray, selected: List[_Token]) -> bool:
    """False when the crop holds full-height ink the selected tokens do not
    account for -- i.e. Tesseract read part of the primary reading and
    dropped the rest.

    ROOT CAUSE this catches (Dataset A sample_0010, reproduced directly):
    the crop plainly renders "34", PSM 11 returns a single token "4" at 96%
    confidence, and there is NOTHING in the recognized text to notice --
    raw_text and matched_text both say "4", so
    crop_integrity.has_residual_content correctly reports nothing
    suspicious. The evidence that a digit went missing is not textual, it
    is geometric: a glyph-sized blob of ink sitting outside every token the
    engine returned.

    Only ink components as TALL as the dominant reading are considered.
    That is what keeps the check from firing on the alarm-limit labels the
    dominant-row selection deliberately excluded -- they are, by the same
    measurement dominant_row_tokens relies on, a fraction of the primary
    glyph height (see _SPARSE_CONFIG's comment block). A short component is
    not a dropped digit; it is the label this reader is supposed to ignore.
    """
    if not selected:
        return True
    anchor_height = max(t.height for t in selected)
    left = min(t.left for t in selected)
    right = max(t.left + t.width for t in selected)
    top = min(t.top for t in selected)
    bottom = max(t.bottom for t in selected)
    tolerance = _UNCOVERED_INK_TOLERANCE * anchor_height

    ink = (processed < 128).astype(np.uint8)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    ink_left: Optional[int] = None
    ink_right: Optional[int] = None
    for index in range(1, count):
        x, y, w, h, _area = stats[index]
        if h < DOMINANT_ROW_HEIGHT_RATIO * anchor_height:
            continue  # a label or speckle, not a dropped digit
        overlap = min(bottom, y + h) - max(top, y)
        if overlap < DOMINANT_ROW_OVERLAP_MIN * min(h, bottom - top):
            continue  # a different row entirely -- not part of this reading
        ink_left = x if ink_left is None else min(ink_left, x)
        ink_right = x + w if ink_right is None else max(ink_right, x + w)

    if ink_left is None:
        return True
    # Compare EXTENTS rather than asking "is any component entirely outside
    # the tokens": adjacent digits on a monitor frequently touch after
    # thresholding, so a dropped digit is often part of the SAME connected
    # component as the one that was read (Dataset A sample_0010's "34" is one
    # blob). What is unambiguous either way is that the ink runs wider than
    # the text the engine claimed for it.
    return bool((left - ink_left) <= tolerance and (ink_right - right) <= tolerance)


def _covers_the_ink(token: _Token, processed: np.ndarray, pad: int) -> bool:
    """True when `token` accounts for essentially all of the ink in the
    preprocessed crop.

    This is the guard on _read_scalar's single-character fallback, and it
    exists because PSM 10 answers the question it is asked ("what single
    character is this?") even when the image plainly holds two: on Dataset
    B it read a genuine "40" as "4" and a genuine "16" as "1" -- a
    confidently-wrong TRUNCATION, the exact failure class this project has
    already been bitten by twice (see app.validation.crop_integrity's
    docstring). A genuinely single-digit reading (a monitor showing HR 0
    during asystole, RR 4 during apnoea -- both real, both in Dataset A)
    has one glyph and therefore one token spanning all of the crop's ink;
    a truncated two-digit read leaves ink outside the token it returned.
    Comparing the token's own width against the thresholded image's ink
    columns separates those two cases directly, from geometry, without
    asking whether the resulting NUMBER looks plausible."""
    inner = processed[pad : processed.shape[0] - pad, pad : processed.shape[1] - pad]
    if inner.size == 0:
        return False
    ink_columns = np.where(np.any(inner < 128, axis=0))[0]
    if ink_columns.size == 0:
        return False
    span = max(1, int(ink_columns[-1] - ink_columns[0]))
    return token.width >= _INK_COVERAGE_MIN * span


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
        value, confidence, _diag = self._read_vital_diag(crop, vital_type)
        return value, confidence

    def read_vital_with_diagnostics(
        self, crop: np.ndarray, vital_type: str, variant: str = "otsu"
    ) -> Tuple[OcrValue, float, OcrDiagnostics]:
        return self._read_vital_diag(crop, vital_type, variant=variant)

    def _read_vital_diag(
        self, crop: np.ndarray, vital_type: str, variant: str = "otsu"
    ) -> Tuple[OcrValue, float, OcrDiagnostics]:
        """The real dispatch. read_vital and read_vital_with_diagnostics are
        both thin views onto this one implementation, so M5.4.1 adds a
        diagnostics channel WITHOUT creating two divergent code paths that
        could read the same crop differently. M5.7.2's `variant` param
        (default 'otsu', unchanged behaviour) follows the same discipline.

        M5.8: every scalar vital now routes through the SAME _SPARSE_CONFIG
        dominant-row read -- the per-vital PSM routing above
        (_DIGIT_CONFIG/_DIGIT_PSM10_CONFIG/_ETCO2_CONFIG/_DECIMAL_CONFIG)
        is retained as the historical record the M4.5/M4.6 eval scripts and
        app.config_snapshot still reference, but is no longer what
        production reads with. Those constants were chosen by measuring
        which single-blob PSM least often mangled a crop containing a
        value AND its alarm labels; the dominant-row reader removes the
        labels before parsing instead, which makes the per-vital choice
        moot (measured: it beats every one of them on the real camera
        frames -- see _SPARSE_CONFIG's comment block). One config for all
        six fields is also one fewer thing that can silently diverge
        between the calibration Verify path and the live path."""
        if vital_type == "nibp":
            return self._read_nibp(crop, variant=variant)
        return self._read_scalar(
            crop, _SPARSE_CONFIG, decimal=(vital_type == "temp"), variant=variant, single_char_fallback=True
        )

    def _run_ocr_on_image(self, image: np.ndarray, config: str) -> dict:
        try:
            return pytesseract.image_to_data(image, config=config, output_type=Output.DICT)
        except pytesseract.TesseractError:
            return {"text": [], "conf": []}

    def _read_scalar(
        self,
        crop: np.ndarray,
        config: str,
        decimal: bool,
        variant: str = "otsu",
        single_char_fallback: bool = False,
    ) -> Tuple[Optional[float], float, OcrDiagnostics]:
        """M5.8: the recognized text is now the crop's DOMINANT ROW only
        (dominant_row_tokens) rather than every token Tesseract emitted
        concatenated together -- see _SPARSE_CONFIG's comment block for the
        root cause and the measured evidence. raw_text/matched_text
        therefore describe the primary reading specifically, which is also
        what makes app.validation.crop_integrity's residual check meaningful
        again: it now compares the VALUE's own recognized text against the
        parsed value, not the value against a blob that always contains
        unrelated label text.

        single_char_fallback (M5.8): PSM 11's layout analysis returns
        NOTHING for a crop whose entire content is one isolated character
        (measured on Dataset A: HR "0", RR "4" -- the exact readings a
        monitor shows during asystole/apnoea, so this is not a rare shape).
        PSM 10 exists for precisely that case, so it is consulted when the
        sparse pass found no text at all -- and its result is accepted ONLY
        if it really is a single character. That restriction is what keeps
        the old failure mode out: PSM 10's damaging behaviour was reading a
        LABEL-CONTAINING crop as one long spliced run ("30" + "8" + "12" ->
        "42"), which by construction is never one character."""
        processed = _preprocess(crop, pad=_QUIET_ZONE_PAD, variant=variant)
        if processed is None:
            return None, 0.0, OcrDiagnostics()
        tokens = _extract_tokens_with_boxes(self._run_ocr_on_image(processed, config))
        selected = dominant_row_tokens(tokens)
        if not selected and single_char_fallback:
            single = [
                t for t in _extract_tokens_with_boxes(self._run_ocr_on_image(processed, _DIGIT_PSM10_CONFIG))
                if len(t.text) == 1 and _covers_the_ink(t, processed, _QUIET_ZONE_PAD)
            ]
            selected = dominant_row_tokens(single)
        text, confidence = _row_text_and_confidence(selected)
        if not text:
            return None, 0.0, OcrDiagnostics()
        incomplete = not _dominant_row_is_complete(processed, selected)

        pattern = r"\d+\.\d+" if decimal else r"\d+"
        match = re.search(pattern, text)
        dropped_decimal_point = False
        if not match:
            # Decimal point sometimes gets dropped/misread — fall back to
            # bare digits rather than failing the whole read.
            match = re.search(r"\d+", text)
            if not match:
                return None, 0.0, OcrDiagnostics(raw_text=text, incomplete_row=incomplete)
            dropped_decimal_point = decimal

        matched_text = match.group()
        # Temp is always rendered with exactly one decimal digit (see this
        # module's docstring) -- so when the point itself got dropped, the
        # bare digit run is 10x too large (e.g. "986" for "98.6") rather
        # than merely imprecise. Reinsert it deterministically instead of
        # returning a value that will always fail the physiological-range
        # check. matched_text is left genuinely different from raw_text in
        # this case (not reset to the reconstructed string) so
        # has_residual_content correctly still treats a reconstructed
        # decimal as worth a second look, same as any other raw/matched
        # mismatch.
        if dropped_decimal_point and len(matched_text) >= 2:
            matched_text = f"{matched_text[:-1]}.{matched_text[-1]}"

        try:
            value = float(matched_text)
        except ValueError:
            return None, 0.0, OcrDiagnostics(raw_text=text, incomplete_row=incomplete)
        return value, confidence, OcrDiagnostics(
            raw_text=text, matched_text=matched_text, incomplete_row=incomplete
        )

    def _nibp_rows(self, processed: np.ndarray) -> List[List[_Token]]:
        """NIBP's crop, split into text rows top to bottom, each row's tokens
        read independently.

        TWO complementary splitters, because each one alone has a measured
        failure on real input:

        - `_split_text_lines` (the ink row-projection split this file has
          used since M1) is what stops Tesseract merging digits ACROSS the
          "sys/dia" and mean lines -- the classic "120/78" + "92" ->
          "1206/78" merge, which PSM 11 reproduces on the simulator's grid
          layout if the whole crop is handed to it at once.
        - `_group_rows` (M5.8, from the tokens' own boxes) then splits any
          strip that still holds more than one row, which happens on the
          real monitor where the NIBP slot's three lines (auto-interval
          history, current sys/dia, mean) sit close enough that the ink
          projection sees no gap between them.

        Each strip is re-padded vertically before OCR: _split_text_lines
        slices the quiet zone off the top and bottom of the strip it cuts,
        and PSM 11 will not segment text that runs to the image edge (see
        _QUIET_ZONE_PAD). _NIBP_CONFIG remains the per-strip fallback -- a
        strip IS a single uniform text block, which is exactly what PSM 6
        is for, so it stays useful here even though M5.8 retired it as a
        whole-crop config."""
        strips = _split_text_lines(processed)
        if not strips:
            strips = [processed]
        rows: List[List[_Token]] = []
        for strip in strips:
            padded = cv2.copyMakeBorder(
                strip, _QUIET_ZONE_PAD, _QUIET_ZONE_PAD, 0, 0, cv2.BORDER_CONSTANT, value=255
            )
            tokens = _extract_tokens_with_boxes(self._run_ocr_on_image(padded, _SPARSE_CONFIG))
            if not tokens:
                tokens = _extract_tokens_with_boxes(self._run_ocr_on_image(padded, _NIBP_CONFIG))
            rows.extend(_group_rows(tokens))
        return rows

    def _read_nibp(self, crop: np.ndarray, variant: str = "otsu") -> Tuple[NibpValue, float, OcrDiagnostics]:
        """M5.8: row grouping now comes from Tesseract's own per-token
        bounding boxes (_group_rows) rather than from an ink-projection
        image split (_split_text_lines, kept for app/eval/
        m4_ocr_benchmark.py's frozen comparison arms).

        NIBP is the one crop where the dominant-row rule from _read_scalar
        does NOT apply: its box legitimately holds TWO readings the operator
        wants -- the large "150/80" and the smaller mean "(103)" beneath --
        plus, on this monitor, an auto-interval history line ABOVE them
        ("Auto 1 min 09:21 09:20 151/80 (104)") whose digits are just as
        real as the current reading's. Height alone cannot separate those,
        because the parenthesised mean is often TALLER than the sys/dia
        digits (the brackets overshoot the digit height) -- measured on the
        real camera frames.

        What DOES separate them is structure: the current reading is the
        row containing a "NN/NN" slash pattern, and the mean is a bare
        2-3 digit run on a DIFFERENT row. The history line is excluded
        because the mean search prefers the row nearest the sys/dia row
        (the monitor draws the mean directly beneath the reading it belongs
        to, while the history line sits above it) -- M4.4 previously tried
        to express this as "prefer the tallest remaining line", which
        picked the history line on real crops.

        Measured on the real camera frames (8 GT-scored NIBP fields): 2
        correct / 5 wrong before, 5 correct / 1 wrong after -- the wrong
        reads were the history line's "151/80 (104)" being reported as the
        current "150/80", and a clipped leading digit reading "451/80".
        """
        processed = _preprocess(crop, pad=_QUIET_ZONE_PAD, variant=variant)
        if processed is None:
            return NibpValue(None, None, None), 0.0, OcrDiagnostics()

        rows = self._nibp_rows(processed)
        if not rows:
            return NibpValue(None, None, None), 0.0, OcrDiagnostics()

        systolic = diastolic = mean = None
        sys_dia_confidence = 0.0
        sys_dia_raw = ""
        sys_dia_match = None
        sys_dia_row_index: Optional[int] = None

        for index, row in enumerate(rows):
            # Digit-free tokens are dropped from this row's text before the
            # match, because the NIBP slot's own field label lives ON the
            # sys/dia line by design -- the monitor renders "Sys. 150/80",
            # and Tesseract returns "Sys."/"sve"/"m" as its own separate
            # token. Leaving it in made raw_text permanently disagree with
            # matched_text, so app.validation.crop_integrity flagged EVERY
            # NIBP read as suspicious (measured on all five real-camera
            # frames that read NIBP correctly) and the live path could never
            # confirm a blood pressure. A label token is distinguishable
            # from a clipped digit's garbage by construction: it contains no
            # digits at all, whereas the truncation this project has
            # measured twice ("8g", "8B") is always fused INTO the digit
            # token, never separated from it.
            digit_tokens = [t for t in row if any(ch.isdigit() for ch in t.text)]
            text, confidence = _row_text_and_confidence(digit_tokens or row, sep=" ")
            match = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", text)
            if match is None:
                continue
            # The CURRENT reading is the LAST slash row, not the first: the
            # history line ("09:20 151/80 (104)") is drawn above the live
            # "Sys. 150/80" line whenever it is inside the box at all.
            systolic = float(match.group(1))
            diastolic = float(match.group(2))
            sys_dia_confidence = confidence
            sys_dia_raw = text
            sys_dia_match = match
            sys_dia_row_index = index

        if sys_dia_row_index is not None:
            # Mean: the nearest row BELOW the current reading that holds a
            # bare 2-3 digit run. Searching downward-only (rather than "any
            # remaining row") is what keeps the history line's own mean out.
            for row in rows[sys_dia_row_index + 1:]:
                text, _confidence = _row_text_and_confidence(row, sep=" ")
                if "/" in text:
                    continue
                mean_match = re.search(r"\d{2,3}", text)
                if mean_match:
                    mean = float(mean_match.group())
                    break

        value = NibpValue(systolic=systolic, diastolic=diastolic, mean=mean)
        # Confidence and diagnostics describe the sys/dia row specifically --
        # never the mean row. M4.4 (root-caused in
        # TIER2_M4_3_RELIABILITY_REPORT.md §8) established this: averaging in
        # the smaller-font, independently noisier mean line's confidence
        # dragged a genuinely-good sys/dia read below CONFIDENCE_MEDIUM_MIN
        # nearly every tick. It stays safe for `mean` because mean's own
        # RANGE_BOUNDS ((20, 220)) rejects implausible means independently of
        # whatever confidence they are paired with.
        diag = OcrDiagnostics(
            raw_text=sys_dia_raw,
            matched_text=sys_dia_match.group() if sys_dia_match else None,
        )
        return value, sys_dia_confidence, diag
