"""M5.1 regression tests: `tessedit_char_whitelist` collapsing Tesseract's
reported confidence toward 0 on correct reads (docs/M5_1_OCR_CONFIDENCE_REPORT.md).

Two layers, because the bug does not reproduce on synthetic/simulator crops
(clean, noise-free renders don't trigger it -- see test_ocr.py's existing
clean-frame tests, which passed both before and after the M5.1 fix):

  1. A structural guard (`test_scalar_configs_carry_no_whitelist`) so the
     whitelist can never be silently reintroduced into the vitals it was
     removed from.
  2. A real-evidence regression (`test_hr_confidence_no_longer_collapses_on_known_case`)
     against the actual Dataset B crop the M5.1 report cites (sample_0009,
     hr) -- reads it with production's current (whitelist-free) config and
     confirms confidence is no longer collapsed, then reads the SAME crop
     with the pre-M5.1 whitelisted config string (reconstructed locally,
     not imported -- production no longer defines it anywhere) to confirm
     the collapse mechanism itself still reproduces, so this test would
     catch a regression back to whitelisted configs even after the old
     constant is long gone from ocr.py.
"""

import os

import numpy as np
import pytest
from PIL import Image

from app.pipeline.detect import detect_screen
from app.pipeline.ocr import (
    _extract_tokens,
    _joined_text_and_confidence,
    _preprocess,
    TesseractEngine,
    _DECIMAL_CONFIG,
    _DIGIT_CONFIG,
    _DIGIT_PSM10_CONFIG,
)

# The quiet-zone border _preprocess used when M5.1 measured the
# whitelist-confidence collapse. M5.8 widened production's to
# ocr._QUIET_ZONE_PAD; this test deliberately pins the historical value --
# see the comparison block below.
_M5_1_QUIET_ZONE_PAD = 20

_DATASET_B_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "eval", "tier2_data", "external_monitor_B",
)


def test_scalar_configs_carry_no_whitelist():
    """Structural guard: none of the three configs M5.1 fixed may ever carry
    `tessedit_char_whitelist` again -- see ocr.py's own M5.1 comment block
    for why. NIBP/EtCO2 were already whitelist-free since M4.4 and are
    covered by tests/test_m4_6_production_promotion.py's own assertions."""
    for config in (_DIGIT_CONFIG, _DECIMAL_CONFIG, _DIGIT_PSM10_CONFIG):
        assert "tessedit_char_whitelist" not in config


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(_DATASET_B_DIR, "sample_0009.png")),
    reason="Dataset B sample not present in this checkout",
)
def test_hr_confidence_no_longer_collapses_on_known_case():
    """Reproduces the exact case cited in ocr.py's M5.1 comment and in
    docs/M5_1_OCR_CONFIDENCE_REPORT.md: Dataset B sample_0009's hr crop reads
    the genuinely correct value at confidence 0 under the old whitelisted
    config, and a real, restored confidence without the whitelist. Same
    crop, same PSM -- whitelist presence/absence is the only variable.

    M5.8: this now exercises the whitelist-free config through
    _read_scalar directly instead of through read_vital(), because
    production's dispatch no longer routes HR to _DIGIT_CONFIG at all -- it
    reads every scalar through the sparse dominant-row path (see
    tests/test_m4_6_production_promotion.py's module docstring). The
    whitelist-vs-no-whitelist COMPARISON this test exists to protect is
    unchanged and still runs on the identical crop at the identical PSM;
    only the caller changed. (On this particular tight Dataset B crop the
    M5.8 production path returns no reading at all -- a measured,
    disclosed read-rate trade documented in
    docs/M5_8_REAL_CAMERA_OBSERVATION.md, not a confidence regression.)"""
    png_path = os.path.join(_DATASET_B_DIR, "sample_0009.png")
    img = np.array(Image.open(png_path).convert("RGB"))
    screen = detect_screen(img)  # this dataset never rectifies (0/17, see EVIDENCE.md sec 2) -- falls back to img unchanged
    x, y, w, h = 1740, 255, 160, 95  # sample_0009.json's own "hr" roi box
    crop = screen.image[y : y + h, x : x + w]

    engine = TesseractEngine()
    value, confidence, _diag = engine._read_scalar(crop, _DIGIT_CONFIG, decimal=False)

    assert value == 85.0  # this dataset's manually-transcribed GT (m5_ground_truth_values.json)
    assert confidence > 10.0, (
        f"production hr confidence on a known-correct read collapsed to {confidence} -- "
        "the M5.1 whitelist-removal fix appears to have regressed"
    )

    # Confirm the OLD (pre-M5.1) whitelisted config still collapses confidence
    # on this exact crop -- proves this is the same mechanism, not a
    # coincidental confidence swing, and keeps the regression detectable even
    # once the whitelisted string is long gone from ocr.py itself.
    #
    # M5.4.1: _read_scalar returns a 3-tuple (value, confidence,
    # OcrDiagnostics) -- see ocr.py's OcrDiagnostics/read_vital_with_diagnostics.
    #
    # M5.8: this comparison is now run against the ORIGINAL 20px quiet-zone
    # preprocessing M5.1 measured, using the same primitives M5.1's own eval
    # script (app/eval/m5_1_ocr_config_sweep.py) uses, rather than through
    # _read_scalar. Reason, measured directly: the whitelist-confidence
    # collapse this test documents is specific to that preprocessing --
    # at M5.8's wider quiet zone (_QUIET_ZONE_PAD) THIS Tesseract build
    # reports 45.0 for the same crop with AND without the whitelist, so the
    # comparison would silently stop testing anything. Pinning the
    # historical preprocessing keeps the drift guard genuinely able to
    # detect a return of the collapse.
    old_whitelisted_config = "--psm 8 -c tessedit_char_whitelist=0123456789"
    m5_1_processed = _preprocess(crop, pad=_M5_1_QUIET_ZONE_PAD)
    _text_wl, old_confidence = _joined_text_and_confidence(
        _extract_tokens(engine._run_ocr_on_image(m5_1_processed, old_whitelisted_config))
    )
    _text_nowl, m5_1_confidence = _joined_text_and_confidence(
        _extract_tokens(engine._run_ocr_on_image(m5_1_processed, _DIGIT_CONFIG))
    )
    assert old_confidence < m5_1_confidence
    assert old_confidence <= 1.0  # matches the "confidence exactly 0" collapse the report documents
