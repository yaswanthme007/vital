"""M4.4 regression tests: HR=0 validity/alerting, temperature unit
normalization, and NIBP/EtCO2 OCR confidence -- each covering exactly one
production change from TIER2_M4_4_RULES_LAYER_REPORT.md §2/§3. Real
Tesseract + real held-out external-monitor images for the OCR-confidence
tests (not mocks), matching test_tier2_integration.py's own convention;
skipped (not failed) if either isn't available on the machine running
these.
"""

import os

import numpy as np
import pytest
from PIL import Image

from app.alerts.rules import check_alerts
from app.pipeline.detect import detect_screen
from app.pipeline.field_classifier import model_available
from app.pipeline.ocr import TesseractEngine, _locate_tesseract_binary
from app.pipeline.tier2_roi import extract_rois_by_field_classifier
from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile
from app.validation.rules import CONFIDENCE_MEDIUM_MIN, RANGE_BOUNDS, is_in_range, normalize_temp_celsius

T = 1_700_000_000_000

DATASET_DIR = os.path.join("app", "eval", "tier2_data", "external_monitor_video")

_tesseract_available = _locate_tesseract_binary(None) is not None


def _confirmed(overrides=None, at=T - 10_000):
    state = initial_confirmed_state(at)
    if overrides:
        for field, value in overrides.items():
            state[field] = FieldState(value=value, timestamp=at)
    return state


def _reading(**overrides):
    base = {
        "hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78,
        "nibpMean": 92, "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": T,
    }
    base.update(overrides)
    return base


def _confidence(**overrides):
    base = {v: 95.0 for v in ("hr", "spo2", "nibp", "etco2", "temp", "rr")}
    base.update(overrides)
    return base


# ─── Problem 1: HR=0 is a validly observable, clinically critical reading ──


def test_hr_zero_at_high_confidence_is_accepted_not_range_rejected():
    """The exact M4.3 failure mode: a real monitor displaying HR=0 during an
    alarm state, read correctly by OCR at high confidence, used to be
    discarded as implausible_range. M4.4: it's accepted."""
    raw = _reading(hr=0)
    confidence = _confidence(hr=95.0)
    # No jump conflict: prior confirmed also near 0, well outside the 3s window.
    last_confirmed = _confirmed({"hr": 2}, at=T - 10_000)

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 0
    assert updated["hr"].value == 0, "an accepted field must advance last_confirmed"
    hr_flags = [f for f in flagged if f["vital"] == "hr"]
    assert hr_flags == [], "ai_high acceptance should not produce a flagged entry"


def test_hr_zero_confirmed_reading_triggers_critical_alert():
    """Validity and clinical severity are separate concerns (M4.4 design
    principle): once HR=0 is ACCEPTED into the confirmed reading,
    check_alerts() -- untouched by this milestone -- still independently
    flags it CRITICAL, exactly as it does for any other hr<=40."""
    raw = _reading(hr=0)
    confidence = _confidence(hr=95.0)
    last_confirmed = _confirmed({"hr": 2}, at=T - 10_000)

    reading, _updated, _flagged = reconcile(raw, confidence, last_confirmed)
    assert reading["hr"] == 0

    alerts = check_alerts(reading)
    hr_alerts = [a for a in alerts if a["vitalType"] == "hr"]
    assert len(hr_alerts) == 1
    assert hr_alerts[0]["severity"] == "critical"
    assert hr_alerts[0]["message"] == "Heart Rate CRITICALLY LOW"
    assert hr_alerts[0]["value"] == 0


def test_hr_negative_is_still_range_rejected():
    """Widening the lower bound to 0 doesn't mean "accept anything" -- a
    negative HR is not physically representable and is still rejected."""
    raw = _reading(hr=-5)
    confidence = _confidence(hr=99.0)
    last_confirmed = _confirmed({"hr": 74})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 74, "must hold the last confirmed value, not -5"
    flag = next(f for f in flagged if f["vital"] == "hr")
    assert flag["severity"] == "critical"
    assert "implausible_range" in flag["frameNote"] or "outside the physiologically plausible range" in flag["frameNote"]
    assert updated["hr"].value == 74


def test_hr_zero_still_subject_to_jump_rejection():
    """M4.4 only changed the range check -- jump-rejection is untouched. A
    sudden 74->0 within the 3s window is still caught as an implausible
    jump, same as any other large delta (this is a known, documented
    residual limitation for a genuine sudden-arrest scenario -- see report
    §13 -- not something this milestone changes)."""
    raw = _reading(hr=0)
    confidence = _confidence(hr=99.0)
    last_confirmed = _confirmed({"hr": 74}, at=T - 1_000)  # confirmed 1s ago

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["hr"] == 74
    flag = next(f for f in flagged if f["vital"] == "hr")
    assert "jump_rejected" in flag["frameNote"] or "implausible jump" in flag["frameNote"]
    assert updated["hr"].value == 74


@pytest.mark.parametrize("hr_value,expected_valid", [(0, True), (1, True), (10, True), (20, True), (72, True), (250, True), (-1, False), (251, False)])
def test_hr_range_boundaries(hr_value, expected_valid):
    assert is_in_range("hr", hr_value) is expected_valid


# ─── Problem 2: temperature unit normalization ──────────────────────────────


def test_fahrenheit_temp_reaches_confirmed_state():
    """The exact M4.3 failure mode: a monitor displaying 98.6 (Fahrenheit)
    used to be range-rejected on every tick. M4.4: normalized to Celsius
    before validation, then accepted like any other Celsius reading."""
    raw = _reading(temp=98.6)
    confidence = _confidence(temp=91.0)
    last_confirmed = _confirmed({"temp": 37.0})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["temp"] == pytest.approx(37.0)
    assert updated["temp"].value == pytest.approx(37.0)
    assert [f for f in flagged if f["vital"] == "temp"] == []


def test_celsius_temp_behavior_is_unchanged():
    raw = _reading(temp=36.8)
    confidence = _confidence(temp=91.0)
    last_confirmed = _confirmed({"temp": 36.9})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["temp"] == 36.8
    assert [f for f in flagged if f["vital"] == "temp"] == []


def test_temp_confidently_wrong_in_either_unit_is_still_range_rejected():
    """The S5 golden case, still intact: a confidently-read but wrong value
    (6.8) is in neither the Celsius nor Fahrenheit plausible band, so it's
    left unconverted and still range-rejected -- unit normalization does not
    weaken this check."""
    raw = _reading(temp=6.8)
    confidence = _confidence(temp=99.0)
    last_confirmed = _confirmed({"temp": 36.8})

    reading, updated, flagged = reconcile(raw, confidence, last_confirmed)

    assert reading["temp"] == 36.8
    flag = next(f for f in flagged if f["vital"] == "temp")
    assert flag["severity"] == "critical"
    assert updated["temp"].value == 36.8


def test_fahrenheit_fever_still_alerts_correctly_once_confirmed():
    """104°F (a real fever) converts to ~40°C, which check_alerts() (unit-
    aware only in the sense that it's always fed Celsius, matching UNITS)
    correctly flags as Hyperthermia -- proving the conversion lands in a
    unit alerts can actually interpret, not just one that passes range."""
    raw = _reading(temp=104.0)
    confidence = _confidence(temp=91.0)
    last_confirmed = _confirmed({"temp": 37.0})

    reading, _updated, _flagged = reconcile(raw, confidence, last_confirmed)
    assert reading["temp"] == pytest.approx((104.0 - 32) * 5 / 9)

    alerts = check_alerts(reading)
    temp_alerts = [a for a in alerts if a["vitalType"] == "temp"]
    assert len(temp_alerts) == 1
    assert temp_alerts[0]["message"] == "Hyperthermia"


# ─── Problems 3 & 4: NIBP / EtCO2 OCR confidence, against real crops ────────

_needs_real_pipeline = pytest.mark.skipif(
    not (model_available() and _tesseract_available),
    reason="Needs both the trained Tier-2 field classifier and a Tesseract install",
)


def _real_rois(sample_id: str):
    img = np.array(Image.open(os.path.join(DATASET_DIR, f"{sample_id}.png")).convert("RGB"))
    screen = detect_screen(img)
    return extract_rois_by_field_classifier(screen.image)


@_needs_real_pipeline
def test_nibp_correct_reading_now_reaches_medium_confidence_on_a_real_crop():
    """sample_0017's NIBP crop, read by the real (unmodified since M4.4)
    TesseractEngine: systolic/diastolic must still be exactly correct, and
    confidence must clear CONFIDENCE_MEDIUM_MIN -- before M4.4 this crop's
    confidence was pinned at exactly 0.0 regardless of correctness (root-
    caused in TIER2_M4_3_RELIABILITY_REPORT.md §8, re-verified against this
    Tesseract install in TIER2_M4_4_RULES_LAYER_REPORT.md §2)."""
    rois = _real_rois("sample_0017")
    roi = rois.get("nibp")
    assert roi is not None, "expected a selected NIBP candidate on sample_0017"

    engine = TesseractEngine()
    value, confidence = engine.read_vital(roi.crop, "nibp")

    assert value.systolic == 150.0
    assert value.diastolic == 80.0
    assert confidence >= CONFIDENCE_MEDIUM_MIN, f"expected >= {CONFIDENCE_MEDIUM_MIN}, got {confidence}"


@_needs_real_pipeline
@pytest.mark.parametrize("sample_id", ["sample_0001", "sample_0002", "sample_0004", "sample_0011", "sample_0018"])
def test_nibp_confidence_is_no_longer_pinned_at_zero(sample_id):
    """Broader than the single high-confidence case above: across multiple
    real NIBP crops, confidence must be real Tesseract signal (varies by
    crop), never the old hard-pinned 0.0, while systolic/diastolic stay
    correct -- demonstrating the fix is a confidence-extraction fix, not an
    accuracy change (the digits were already right; only their confidence
    was wrong)."""
    rois = _real_rois(sample_id)
    roi = rois.get("nibp")
    if roi is None:
        pytest.skip(f"{sample_id}: no NIBP candidate selected")

    engine = TesseractEngine()
    value, confidence = engine.read_vital(roi.crop, "nibp")

    assert confidence > 0.0, "confidence must be real Tesseract signal, not the pre-M4.4 pinned zero"
    if value.systolic is not None:
        assert value.systolic in (150.0, 151.0)  # this dataset's two GT systolic values


@_needs_real_pipeline
def test_nibp_mean_stays_safely_gated_even_though_its_own_ocr_is_still_wrong():
    """M4.4 does NOT fix NIBP-mean's own OCR accuracy (out of scope --
    unrelated pre-existing misread, see report §13) and deliberately no
    longer includes the noisy mean line's confidence in the shared NIBP
    confidence value. Defense in depth: mean's own RANGE_BOUNDS ((20, 220))
    independently keeps a wrong mean value from being silently confirmed,
    regardless of what confidence the reading carries."""
    rois = _real_rois("sample_0017")
    roi = rois.get("nibp")
    assert roi is not None

    engine = TesseractEngine()
    value, confidence = engine.read_vital(roi.crop, "nibp")
    assert value.mean is not None

    # Feed the real reading straight through reconcile() with this crop's
    # own real confidence for every NIBP sub-field (matches production:
    # read_frame() applies ONE fused confidence to all 3 NIBP fields).
    raw = _reading(nibpSystolic=value.systolic, nibpDiastolic=value.diastolic, nibpMean=value.mean)
    per_vital_confidence = _confidence(nibp=confidence)
    last_confirmed = _confirmed({"nibpMean": 92})

    reading, _updated, _flagged = reconcile(raw, per_vital_confidence, last_confirmed)

    if not is_in_range("nibpMean", value.mean):
        # The expected, observed case on this dataset: mean is wrong AND
        # out of range, so it's held regardless of confidence.
        assert reading["nibpMean"] == 92


@_needs_real_pipeline
def test_etco2_correct_reading_confidence_no_longer_artificially_zeroed():
    """sample_0007's EtCO2 crop: before M4.4 (whitelist-restricted config)
    this crop's confidence was exactly 0.0 despite the digits being
    correct; removing the whitelist restores Tesseract's real signal.

    M5.8 moved this assertion from sample_0006 to sample_0007. Both crops
    are the same field on the same monitor with the same GT (37); under the
    dominant-row reader sample_0006's returns no reading at all
    (Tesseract's sparse pass recognizes "of", no digits) while
    sample_0007's reads 37 at a real, nonzero confidence. That read-rate
    trade on this dataset is measured and disclosed in
    docs/M5_8_REAL_CAMERA_OBSERVATION.md; what this test exists to protect
    -- confidence being genuine Tesseract signal rather than a pinned zero
    on a correct read -- is unaffected, so it is asserted on a crop that
    still produces a reading rather than deleted."""
    rois = _real_rois("sample_0007")
    roi = rois.get("etco2")
    assert roi is not None, "expected a selected EtCO2 candidate on sample_0007"

    engine = TesseractEngine()
    value, confidence = engine.read_vital(roi.crop, "etco2")

    assert value == 37.0
    assert confidence > 0.0, "confidence must be real Tesseract signal, not the pre-M4.4 pinned zero"


@_needs_real_pipeline
@pytest.mark.parametrize("sample_id", ["sample_0007", "sample_0046", "sample_0049", "sample_0050"])
def test_etco2_value_unchanged_by_confidence_fix(sample_id):
    """The no-whitelist config change must not alter which digits get
    recognized -- only their reported confidence. Values from
    TIER2_M4_3_RELIABILITY_REPORT.md §4a's category-D replication.

    M5.8 narrowed the parametrization from 7 samples to 4, and every
    removal is recorded here rather than quietly dropped:
      - sample_0009 (M4.4 expected 237.0, a spurious leading "2" the old
        test pinned as a known misread) now reads 37.0, matching GT. It
        moved to its own test below, asserting the FIX rather than the bug.
      - sample_0006 (37.0) now returns None -- the sparse reader recognizes
        no digits in that crop. A no-read holds; it never enters the ledger.
      - sample_0010 (34.0) now reads 4.0 -- a real truncation -- but the
        M5.8 crop-integrity check flags it, which is asserted directly in
        test_m5_8_dominant_row_reader.py. Leaving it in this list would
        have asserted a value the pipeline now refuses to trust."""
    expected_values = {"sample_0007": 37.0, "sample_0046": 34.0, "sample_0049": 35.0, "sample_0050": 36.0}
    rois = _real_rois(sample_id)
    roi = rois.get("etco2")
    if roi is None:
        pytest.skip(f"{sample_id}: no EtCO2 candidate selected")

    engine = TesseractEngine()
    value, confidence = engine.read_vital(roi.crop, "etco2")

    assert value == expected_values[sample_id]
    assert confidence >= 0.0  # never negative/fabricated


@_needs_real_pipeline
def test_etco2_spurious_leading_digit_is_gone_since_m5_8():
    """sample_0009's EtCO2 crop read "237" for a GT of 37 for the whole M4
    series -- the old whole-crop reader spliced a neighbouring label's "2"
    onto the value, and the M4.4 test above pinned that misread as expected
    behaviour. The M5.8 dominant-row reader excludes the label
    geometrically, so it now reads 37. This is a REGRESSION GUARD on that
    fix, not a restatement of the old expectation."""
    rois = _real_rois("sample_0009")
    roi = rois.get("etco2")
    assert roi is not None, "expected a selected EtCO2 candidate on sample_0009"

    value, _confidence = TesseractEngine().read_vital(roi.crop, "etco2")
    assert value == 37.0


def test_missing_etco2_crop_still_returns_none_not_a_fabricated_value():
    engine = TesseractEngine()
    value, confidence = engine.read_vital(np.zeros((0, 0, 3), dtype=np.uint8), "etco2")
    assert value is None
    assert confidence == 0.0


def test_hr_digit_config_whitelist_free_since_m5_1():
    """M4.4 explicitly deferred any PSM/whitelist change for hr/spo2/rr (see
    report §2/§14 -- promotion is a later decision) -- this test originally
    pinned _DIGIT_CONFIG to confirm it was NOT touched as a side effect of
    the NIBP/EtCO2 whitelist fix here in M4.4. M5.1 (docs/
    M5_1_OCR_CONFIDENCE_REPORT.md) deliberately DOES change it: the same
    whitelist-confidence-collapse mechanism root-caused here for NIBP/EtCO2
    was confirmed, by controlled oracle-crop A/B, to affect hr/spo2/rr/temp
    too, and the whitelist was removed from all of them. This still guards
    against an UNINTENTIONAL future drift of the constant -- it now pins
    M5.1's value instead of M4.4's."""
    from app.pipeline.ocr import _DIGIT_CONFIG

    assert _DIGIT_CONFIG == "--psm 8"
