"""M4.6: production promotion of M4.5's evidence-backed selective PSM
routing (SpO2 + RR -> --psm 10, HR left untouched). These tests exercise the
REAL production dispatch mechanism (TesseractEngine.read_vital()) directly
-- no mocking of the config-selection logic itself -- following the same
convention as test_m4_4_rules_layer.py::test_hr_digit_config_whitelist_free_since_m5_1.

Two of the config-string assertions below were updated for M5.1 (docs/
M5_1_OCR_CONFIDENCE_REPORT.md), which removed `tessedit_char_whitelist` from
_DIGIT_PSM10_CONFIG and _DECIMAL_CONFIG -- see those tests' own docstrings.
PSM routing itself (which vital uses which PSM) is exactly what M4.6
promoted.

M5.8 SUPERSEDES THAT ROUTING, and the routing assertions below were
rewritten accordingly -- deliberately, with the reason recorded here rather
than silently deleted. M4.5/M4.6 were choosing between whole-crop PSMs (8
"single word" vs 10 "single character"), each of which returns a crop's
value AND its alarm-limit labels as one text blob; the choice was about
which blob got mangled least. M5.8's dominant-row reader
(app.pipeline.ocr.dominant_row_tokens) removes the labels geometrically
BEFORE parsing, which makes the whole-crop PSM choice moot -- measured
against the demo laptop's own real camera frames plus frozen Dataset A/B,
--psm 11 + dominant-row beats every one of the per-vital routings M4.6
promoted (docs/M5_8_REAL_CAMERA_OBSERVATION.md). The M4.5/M4.6 CONSTANTS
are retained and still asserted below: they remain the configuration the
M4-series eval scripts reproduce, and _DIGIT_PSM10_CONFIG is still used in
production as the guarded single-character fallback.
"""

from unittest.mock import patch

import numpy as np

from app.pipeline.ocr import (
    TesseractEngine,
    _DECIMAL_CONFIG,
    _DIGIT_CONFIG,
    _DIGIT_PSM10_CONFIG,
    _ETCO2_CONFIG,
    _NIBP_CONFIG,
    _PSM10_VITALS,
    _SPARSE_CONFIG,
)


def test_psm10_vitals_are_exactly_spo2_and_rr():
    """The routing set promoted from M4.5 must be exactly {spo2, rr} -- not
    a superset (e.g. accidentally including hr) and not a subset."""
    assert _PSM10_VITALS == {"spo2", "rr"}


def test_digit_psm10_config_matches_m4_5_evidence_plus_m5_1_whitelist_removal():
    """M4.5/M4.6 promoted this PSM-10 string, whitelisted, byte-identical to
    what M4.5 evaluated. M5.1 (docs/M5_1_OCR_CONFIDENCE_REPORT.md) then
    removed the whitelist: on Dataset B oracle crops, whitelisted SpO2 read
    82% of frames correctly at confidence exactly 0 -- this was the worst
    single instance of the whitelist-confidence-collapse mechanism M4.4
    root-caused for NIBP/EtCO2. PSM stays 10, exactly as M4.5 evaluated;
    only the whitelist clause is gone."""
    assert _DIGIT_PSM10_CONFIG == "--psm 10"


def _fake_crop():
    return np.zeros((30, 60, 3), dtype=np.uint8)


def _configs_used_for(vital_type: str) -> list:
    """Call the real TesseractEngine.read_vital() dispatch, capturing every
    config string that actually reaches Tesseract, by patching only the
    Tesseract-calling boundary (_run_ocr_on_image) -- the dispatch/if logic
    under test runs for real and unmocked.

    M5.8: returns the full LIST, not just one config, because the scalar
    path can legitimately make a second, guarded call (the
    single-character fallback) when the sparse pass recognizes nothing --
    which is exactly what an empty fake crop produces."""
    engine = TesseractEngine.__new__(TesseractEngine)  # skip binary lookup
    captured = []

    def fake_run(self, image, config):
        captured.append(config)
        return {"text": [], "conf": []}

    with patch.object(TesseractEngine, "_run_ocr_on_image", fake_run):
        engine.read_vital(_fake_crop(), vital_type)
    return captured


def _config_used_for(vital_type: str) -> str:
    """The PRIMARY config for a vital -- the first one Tesseract is asked
    for. Any later entry is a fallback, asserted separately."""
    configs = _configs_used_for(vital_type)
    return configs[0] if configs else None


def test_every_scalar_vital_reads_through_the_sparse_dominant_row_config():
    """M5.8: HR, SpO2, RR, EtCO2 and Temp all take the SAME primary path
    now. The per-vital PSM routing M4.5/M4.6 promoted was a choice between
    whole-crop readers; the dominant-row reader removes the alarm-limit
    labels that made that choice matter (see this module's docstring)."""
    for vital in ("hr", "spo2", "rr", "etco2", "temp"):
        assert _config_used_for(vital) == _SPARSE_CONFIG, vital


def test_single_character_fallback_is_the_only_secondary_scalar_config():
    """The only config a scalar read may fall back to is PSM 10, and only
    after the sparse pass found nothing -- which an all-zero crop
    guarantees. _DIGIT_PSM10_CONFIG therefore survives M5.8 in production,
    in a narrower role than M4.6 gave it."""
    for vital in ("hr", "spo2", "rr", "etco2", "temp"):
        configs = _configs_used_for(vital)
        assert configs[0] == _SPARSE_CONFIG, vital
        assert set(configs[1:]) <= {_DIGIT_PSM10_CONFIG}, (vital, configs)


def test_no_scalar_vital_uses_the_retired_whole_crop_configs():
    """Negative assertion: the M4-era whole-crop single-word/uniform-block
    configs must not reach Tesseract on the scalar path at all any more."""
    for vital in ("hr", "spo2", "rr", "etco2", "temp"):
        configs = _configs_used_for(vital)
        assert _DIGIT_CONFIG not in configs, vital
        assert _ETCO2_CONFIG not in configs, vital
        assert _NIBP_CONFIG not in configs, vital


def test_nibp_reads_line_strips_with_the_sparse_config_and_falls_back_to_psm6():
    """NIBP still dispatches through _read_nibp, not _read_scalar. Each ink
    row strip is read with the sparse config; _NIBP_CONFIG (PSM 6, "uniform
    block") remains the per-STRIP fallback, which is what it was always
    good at -- a single strip genuinely is one uniform text block."""
    assert _NIBP_CONFIG == "--psm 6"
    assert "nibp" not in _PSM10_VITALS

    engine = TesseractEngine.__new__(TesseractEngine)
    captured_configs = []

    def fake_run(self, image, config):
        captured_configs.append(config)
        return {"text": [], "conf": []}

    with patch.object(TesseractEngine, "_run_ocr_on_image", fake_run):
        engine.read_vital(_fake_crop(), "nibp")

    assert captured_configs, "expected _read_nibp to invoke OCR at least once"
    assert captured_configs[0] == _SPARSE_CONFIG
    assert set(captured_configs) <= {_SPARSE_CONFIG, _NIBP_CONFIG}
    assert _DIGIT_PSM10_CONFIG not in captured_configs


def test_m4_era_config_constants_are_still_recorded_verbatim():
    """The M4.4/M4.5/M4.6/M5.1 constants are the configuration those
    milestones' eval scripts (app/eval/m4_5_selective_psm_reliability.py,
    m4_6_production_promotion.py, m5_1_ocr_config_sweep.py) reproduce, and
    app.config_snapshot still reports them. M5.8 changed which one
    PRODUCTION dispatches to -- it did not rewrite the historical record."""
    assert _DIGIT_CONFIG == "--psm 8"
    assert _DECIMAL_CONFIG == "--psm 8"
    assert _DIGIT_PSM10_CONFIG == "--psm 10"
    assert _ETCO2_CONFIG == "--psm 8"
    assert _NIBP_CONFIG == "--psm 6"
    assert _PSM10_VITALS == {"spo2", "rr"}
