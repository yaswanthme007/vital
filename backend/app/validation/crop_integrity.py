"""M5.4.1: crop-integrity signal -- detects OCR evidence that a crop's true
content was NOT a clean, fully-framed reading, so app.validation.temporal
can refuse to let temporal corroboration promote it.

============================================================================
ROOT CAUSE THIS MODULE ADDRESSES (see docs/M5_4_1_CROP_INTEGRITY_REPORT.md
for the full investigation and evidence)
============================================================================

M5.4's held-out validation (docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md
Phase 6) found a real, confidently-wrong regression: HR truncated "83"/"84"
-> "8" repeated identically at confidence 58-66%, inside the temporal-
corroboration band, with no confidence floor able to separate it from
genuine corroborations. Root-caused directly against the real crops
(app.eval.tier2_data.external_monitor_B, frozen_B[sample_0011] reference):
the calibrated/tracked box for HR is narrower than the digit run it is
meant to contain, so the crop shows the LEADING digit ("8") and cuts off
the trailing one mid-glyph.

Measured directly (not assumed): Tesseract does not simply fail to see the
clipped remainder -- it recognizes SOMETHING there, just not a clean digit.
Running the real production OCR path (--psm 8, whitelist-free, per M5.1) on
these exact crops returns raw text "8g" / "8B", not "8". The digit-
extracting regex in app.pipeline.ocr._read_scalar only keeps the leading
"8" (the longest contiguous digit run) and silently discards the rest --
which is the correct thing for a SINGLE tick (a bare "8" is a plausible,
if wrong, HR value, and reconcile()'s range check cannot tell the
difference), but is exactly the information a REPEATED-read consumer like
temporal corroboration needs and did not have.

Two candidate detectors were evaluated against real data
(app.eval.m5_4_1_crop_integrity_eval) before choosing this one:

  A. Raw ink-column margin (does thresholded ink touch the crop's own
     pixel edge?) -- REJECTED. Measured across frozen_A/frozen_B: many
     GENUINELY CORRECT reads also have ink flush against one or both crop
     edges (calibrated/tracked boxes are frequently drawn or tracked tight
     around the digits by design -- a tight-but-correct box and a
     tight-because-truncated box are visually indistinguishable from raw
     ink geometry alone). This produced far more false holds on correct
     reads than true detections and was dropped.

  B. OCR raw-text residual content (THIS module) -- the engine's own
     recognized text contains characters beyond the parsed digit run.
     Reuses evidence Tesseract already computed (image_to_data's token
     text) instead of re-deriving glyph shape independently, and correctly
     separates "a real digit run touching the box edge because the box
     fits the value" (Tesseract emits ONLY that digit run as its token
     text; nothing to discard) from "a value plus a garbled, clipped
     remainder" (a non-digit tail/prefix is present in the raw text and
     discarded by the value-parsing regex). Still not perfectly clean on
     its own -- see CONSERVATIVE DESIGN below -- so
     app.validation.temporal additionally requires every tick in a
     corroborating run to be clean, not just the current one.

============================================================================
CONSERVATIVE DESIGN, STATED PLAINLY
============================================================================

has_residual_content() is a HIGH-RECALL, MODERATE-PRECISION signal: it
reliably catches the target failure class (measured: 100% of the real
frozen_B[sample_0011] HR "8g"/"8B" ticks), but it also fires on some
correct reads where Tesseract's own OCR noise incidentally produced an
extra stray character (measured false-positive rate:
app.eval.m5_4_1_crop_integrity_eval). That is an intentional, evidence-
weighed tradeoff, not an oversight: per this project's own stated safety
posture (docs/ARCHITECTURE.md), a FALSE HOLD (a correct-but-noisy read
stays held one tick longer) is an acceptable cost; a CONFIDENTLY-WRONG
CONFIRMATION is not. This module can only ever cause reconcile() to hold
MORE, never less -- see app.validation.temporal.is_corroborated, which is
the only caller, and only inside its own already-narrow ai_low branch.
"""

import re
from typing import Optional, Protocol

# Leading/trailing characters stripped from raw_text before it is compared
# with the parsed value (M5.8).
#
# WHY, measured: with M5.8's dominant-row reader
# (app.pipeline.ocr.dominant_row_tokens) the recognized text is now the
# primary reading's own row rather than a blob of the whole crop -- but on
# a real webcam frame that row still routinely picks up one stray symbol
# glyph at its edge, because a monitor draws the field's waveform, its
# separator rules and the neighbouring label's tail right up against the
# digits. Measured on the demo laptop's own reference frames, correct reads
# come back as '"88', '\?98', '#34', '* 150/80', '98.6|', and (where
# Tesseract could not name the glyph at all) '�88' -- every one of them
# the CORRECT value plus one non-alphanumeric artifact.
#
# The failure class this module actually exists to catch is different and
# was measured directly (see the root-cause section above): a clipped digit
# is recognized as a LETTER or another DIGIT -- "8g", "8B" -- never as
# punctuation or an unrecognized-glyph placeholder. So the residual that
# carries signal is ALPHANUMERIC residue, and stripping non-alphanumeric
# noise from the ends narrows the detector to exactly the evidence it was
# built on rather than weakening it: "8g"/"8B" are still flagged, and so is
# any residue in the MIDDLE of the recognized text (only the ends are
# stripped, never the interior).
_EDGE_NOISE = re.compile(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$")


def has_residual_content(raw_text: str, matched_text: Optional[str]) -> bool:
    """True when the OCR engine's raw recognized text contains content
    beyond the substring that was actually parsed into a value -- e.g.
    raw_text="8g", matched_text="8" -> True (a "g" was recognized and
    discarded); raw_text="86", matched_text="86" -> False (nothing was
    discarded, the digit run IS the whole recognized token).

    raw_text="" (nothing recognized at all -- OCR returned no text, which
    already surfaces as raw_value=None upstream and resets the temporal run
    via app.validation.temporal.observe) and matched_text=None (a value
    could not be parsed at all, same "None" outcome) both return False:
    there is nothing to compare, so this function reports nothing
    suspicious rather than guessing. It is deliberately NOT this function's
    job to flag "unreadable" -- reconcile()'s existing "unreadable" ->
    "HOLD" path already covers that, unconditionally, with or without this
    module (see app.validation.reconcile.reconcile's per-field loop, the
    `raw_value is None` branch, which runs before confidence/temporal are
    ever consulted)."""
    if not raw_text or matched_text is None:
        return False
    return _EDGE_NOISE.sub("", raw_text) != _EDGE_NOISE.sub("", matched_text)


class _HasOcrDiagnostics(Protocol):
    """Structural type for app.pipeline.ocr.OcrDiagnostics -- declared here
    rather than imported so this validation module keeps its existing
    zero-dependency-on-the-pipeline shape."""

    raw_text: str
    matched_text: Optional[str]
    incomplete_row: bool


def crop_is_suspicious(diagnostics: _HasOcrDiagnostics) -> bool:
    """The single entry point every caller should use: True when EITHER
    integrity signal fires for this read.

    Two signals, because each catches a truncation the other cannot:

      - has_residual_content (M5.4.1, above) -- TEXTUAL. The engine
        recognized characters beyond the parsed value ("8g" for a value of
        8), so the discard is visible in the text itself.
      - OcrDiagnostics.incomplete_row (M5.8) -- GEOMETRIC. The crop holds
        full-height ink the recognized tokens do not account for. Measured
        on Dataset A sample_0010: a clearly-rendered "34" is tokenized as
        just "4" at 96% confidence, with raw_text == matched_text == "4".
        Nothing textual is left over to notice; the missing "3" is only
        visible as unclaimed ink.

    Same conservative direction as the rest of this module: it can only
    ever cause a caller to hold MORE, never less."""
    return bool(getattr(diagnostics, "incomplete_row", False)) or has_residual_content(
        diagnostics.raw_text, diagnostics.matched_text
    )
