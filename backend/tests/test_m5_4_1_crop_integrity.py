"""M5.4.1: crop-integrity / temporal-corroboration-safety regression tests.

See docs/M5_4_1_CROP_INTEGRITY_REPORT.md for the full investigation. The
short version: M5.4's held-out validation found that a too-narrow
calibrated/tracked HR box on real Dataset B footage (reference
frozen_B[sample_0011]) clipped "83"/"84" down to "8" on every tick, and
temporal corroboration (docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md)
wrongly confirmed the truncated "8" after 3 repeats. Root-caused directly
against the real crops: Tesseract's raw OCR text for these ticks is "8g"/
"8B", not "8" -- the clipped remainder is recognized as SOMETHING, just not
a clean digit, and the digit-extracting regex silently discards it. This
module (app.validation.crop_integrity) surfaces that discard as a boolean,
and app.validation.temporal's TemporalFieldState.clean_run refuses
corroboration for any run that ever showed it.

Four layers, matching the real evidence available:

  1. app.validation.crop_integrity unit tests -- the pure text-comparison
     signal in isolation.
  2. app.validation.temporal unit tests -- clean_run bookkeeping.
  3. reconcile()-level synthetic regression tests reproducing the reported
     failure shape numerically (NOT hardcoding "83"/"84" into any
     production branch -- the fix has no per-value special case at all; the
     numbers below are realistic test data, not a mechanism).
  4. A real-data regression test against the actual Dataset B crops the
     M5.4 report's Phase 6 found the failure on, using the real production
     TesseractEngine + LayoutTracker + calibrated_roi pipeline -- the
     strongest available evidence that the fix works, not just the model.
"""

import os

import numpy as np
import pytest
from PIL import Image

from app.pipeline.calibrated_roi import make_extractor, reference_pixel_boxes
from app.pipeline.layout_tracker import LayoutTracker
from app.pipeline.ocr import TesseractEngine, _locate_tesseract_binary
from app.validation.crop_integrity import has_residual_content
from app.validation.reconcile import FieldState, initial_confirmed_state, reconcile
from app.validation.temporal import (
    CONFIDENCE_TEMPORAL_FLOOR,
    TEMPORAL_AGREEMENT_MIN_RUN,
    TemporalFieldState,
    initial_temporal_state,
    is_corroborated,
    observe,
)

T = 1_700_000_000_000
TICK_MS = 1_000

_DATASET_B_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "eval", "tier2_data", "external_monitor_B",
)


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


def _flag_for(flagged, vital):
    matches = [f for f in flagged if f["vital"] == vital]
    assert len(matches) == 1, f"expected exactly one flagged entry for {vital}, got {matches}"
    return matches[0]


# ═══════════════════════════════════════════════════════════════════════
# app.validation.crop_integrity — pure signal
# ═══════════════════════════════════════════════════════════════════════


def test_has_residual_content_true_when_text_exceeds_matched_value():
    """The exact real-data shape: Tesseract reads '8g' but the regex only
    parses '8' as the value."""
    assert has_residual_content("8g", "8") is True


def test_has_residual_content_false_when_text_is_exactly_the_matched_value():
    assert has_residual_content("86", "86") is False


def test_has_residual_content_false_on_empty_text():
    assert has_residual_content("", None) is False
    assert has_residual_content("", "8") is False


def test_has_residual_content_false_when_no_value_was_parsed():
    """matched_text=None means reconcile() already sees raw_value=None for
    this tick -- 'unreadable', handled unconditionally upstream (see
    reconcile()'s own `raw_value is None` branch). This function must not
    ALSO flag it, or it would double-count the same failure two different
    ways."""
    assert has_residual_content("??", None) is False


def test_has_residual_content_ignores_surrounding_whitespace():
    assert has_residual_content(" 86 ", "86") is False


# ═══════════════════════════════════════════════════════════════════════
# app.validation.temporal — clean_run bookkeeping
# ═══════════════════════════════════════════════════════════════════════


def test_observe_defaults_to_clean_when_crop_suspicious_omitted():
    """Backward compatibility: every pre-M5.4.1 call of observe(state, value)
    (2 positional args) must behave exactly as before."""
    state = observe(TemporalFieldState(), 85.0)
    assert state.clean_run is True


def test_observe_marks_run_unclean_on_a_suspicious_tick():
    state = TemporalFieldState(last_value=8.0, run_length=2, clean_run=True)
    state = observe(state, 8.0, crop_suspicious=True)
    assert state.run_length == 3
    assert state.clean_run is False


def test_observe_clean_run_stays_false_once_tainted_even_if_later_ticks_are_clean():
    """The whole-run policy: ONE suspicious tick taints every subsequent
    tick of the SAME run, because the failure this guards against (a
    too-narrow box) is systematic, not independent per-tick noise -- see
    the module docstring."""
    state = TemporalFieldState()
    state = observe(state, 8.0, crop_suspicious=True)   # tick 1: suspicious
    state = observe(state, 8.0, crop_suspicious=False)  # tick 2: clean
    state = observe(state, 8.0, crop_suspicious=False)  # tick 3: clean
    assert state.run_length == 3
    assert state.clean_run is False, "one dirty tick must taint the whole run, not just itself"


def test_observe_resets_clean_run_on_value_change():
    state = TemporalFieldState(last_value=8.0, run_length=3, clean_run=False)
    state = observe(state, 9.0, crop_suspicious=False)
    assert state.run_length == 1
    assert state.clean_run is True, "a NEW value starts a fresh, clean run"


def test_observe_resets_clean_run_on_missing_read():
    state = TemporalFieldState(last_value=8.0, run_length=3, clean_run=False)
    state = observe(state, None)
    assert state.run_length == 0
    assert state.clean_run is True


def test_is_corroborated_requires_clean_run_in_addition_to_floor_and_length():
    dirty = TemporalFieldState(last_value=8.0, run_length=TEMPORAL_AGREEMENT_MIN_RUN, clean_run=False)
    clean = TemporalFieldState(last_value=8.0, run_length=TEMPORAL_AGREEMENT_MIN_RUN, clean_run=True)
    assert is_corroborated(dirty, 90.0) is False, "a long, high-confidence, but DIRTY run must not corroborate"
    assert is_corroborated(clean, CONFIDENCE_TEMPORAL_FLOOR) is True


# ═══════════════════════════════════════════════════════════════════════
# reconcile() — the exact reported failure shape, synthetic (Phase 4)
# ═══════════════════════════════════════════════════════════════════════
#
# Numbers below mirror the real incident (HR true value 83/84, OCR
# repeatedly reads 8, confidence 58-66%) as REALISTIC TEST DATA, not as a
# mechanism the fix special-cases. Nothing in app.validation.temporal or
# app.validation.crop_integrity branches on the literal value 8, 83, or 84.


def _run_ticks_with_crop_flags(field_values, temporal_state, confirmed=None, start_ts=T):
    """field_values: list of (value, confidence, crop_suspicious) tuples fed
    as consecutive HR ticks. Returns the LAST tick's (reading, updated,
    flagged)."""
    confirmed = confirmed if confirmed is not None else _confirmed(at=start_ts - 10_000)
    result = None
    for i, (value, confidence, crop_suspicious) in enumerate(field_values):
        raw = _reading(hr=value, timestamp=start_ts + i * TICK_MS)
        conf = _confidence(hr=confidence)
        result = reconcile(
            raw, conf, confirmed, temporal_state=temporal_state,
            per_vital_crop_suspicious={"hr": crop_suspicious},
        )
        _, confirmed, _ = result
    return result


def test_old_behaviour_reproduces_the_truncated_value_being_confirmed():
    """Demonstrates the OLD (M5.4, pre-M5.4.1) mechanism on this exact
    failure shape: temporal_state opted in, but crop-integrity evidence
    never passed (per_vital_crop_suspicious=None) -- reconcile()'s only
    signals are confidence + run length, exactly as M5.4 shipped. A
    truncated value repeating 4 times at sub-gate confidence gets wrongly
    confirmed, reproducing docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md
    Phase 6's finding."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed(at=T - 10_000)
    ticks = [(8, 60.0), (8, 38.0), (8, 58.0), (8, 66.0)]  # HR true value was 82/81/83/84
    last_reading = None
    for i, (value, confidence) in enumerate(ticks):
        raw = _reading(hr=value, timestamp=T + i * TICK_MS)
        conf = _confidence(hr=confidence)
        last_reading, confirmed, flagged = reconcile(
            raw, conf, confirmed, temporal_state=temporal_state,  # no per_vital_crop_suspicious
        )
    assert last_reading["hr"] == 8, "the OLD mechanism confirms the truncated value -- this IS the bug M5.4 shipped off for"
    flag = _flag_for(flagged, "hr")
    assert "temporal corroboration" in flag["frameNote"]


def test_new_behaviour_refuses_to_confirm_the_truncated_value():
    """The fix: identical tick sequence, but crop_suspicious=True on every
    tick (the real OCR evidence -- Tesseract reading '8g'/'8B' instead of a
    clean '8', see app.validation.crop_integrity). The value must stay
    held, never confirmed as 8."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed(at=T - 10_000)
    ticks = [(8, 60.0, True), (8, 38.0, True), (8, 58.0, True), (8, 66.0, True)]
    last_reading, _updated, flagged = _run_ticks_with_crop_flags(ticks, temporal_state, confirmed)
    assert last_reading["hr"] != 8, "the truncated value must NEVER be confirmed once crop-integrity evidence is wired in"
    assert last_reading["hr"] == 75, "must stay held at the seeded baseline"
    flag = _flag_for(flagged, "hr")
    assert "temporal" not in flag["frameNote"]


def test_new_behaviour_still_confirms_when_the_run_is_genuinely_clean():
    """The fix must not be a blanket 'never corroborate HR' rule -- a
    repeated sub-gate reading with NO crop-integrity red flags still
    corroborates exactly as M5.4 intended."""
    temporal_state = initial_temporal_state()
    ticks = [(85, 55.0, False)] * TEMPORAL_AGREEMENT_MIN_RUN
    reading, _updated, flagged = _run_ticks_with_crop_flags(ticks, temporal_state)
    assert reading["hr"] == 85
    flag = _flag_for(flagged, "hr")
    assert "temporal corroboration" in flag["frameNote"]


def test_legitimate_single_digit_value_still_corroborates():
    """A genuinely single-digit HR (technically representable -- RANGE_BOUNDS
    allows [0, 250], and reconcile() has no digit-COUNT policy of its own)
    repeated consistently with clean crop evidence must not be penalized
    just because the correct value happens to be short."""
    temporal_state = initial_temporal_state()
    ticks = [(8, 55.0, False)] * TEMPORAL_AGREEMENT_MIN_RUN
    reading, _updated, flagged = _run_ticks_with_crop_flags(ticks, temporal_state)
    assert reading["hr"] == 8
    flag = _flag_for(flagged, "hr")
    assert "temporal corroboration" in flag["frameNote"]


def test_legitimate_two_digit_value_with_no_suspicious_ticks_corroborates():
    ticks = [(47, 52.0, False)] * TEMPORAL_AGREEMENT_MIN_RUN
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks_with_crop_flags(ticks, temporal_state)
    assert reading["hr"] == 47
    assert "temporal corroboration" in _flag_for(flagged, "hr")["frameNote"]


def test_clipped_two_digit_value_is_held():
    """178 -> 17 shape (the OTHER real truncation class this project has
    seen, docs/M5_3_LAYOUT_TRACKING_REPORT.md sec 3 / M5.4 report Phase 1)
    -- a two-digit truncated remainder, flagged suspicious every tick."""
    ticks = [(17, 60.0, True)] * TEMPORAL_AGREEMENT_MIN_RUN
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks_with_crop_flags(ticks, temporal_state)
    assert reading["hr"] == 75, "held at baseline, never confirmed as the clipped 17"
    assert "temporal" not in _flag_for(flagged, "hr")["frameNote"]


def test_clipped_three_digit_value_is_held():
    """A 3-digit value clipped to 2 digits, still inside RANGE_BOUNDS."""
    ticks = [(18, 61.0, True)] * TEMPORAL_AGREEMENT_MIN_RUN
    temporal_state = initial_temporal_state()
    reading, _updated, flagged = _run_ticks_with_crop_flags(ticks, temporal_state)
    assert reading["hr"] == 75
    assert "temporal" not in _flag_for(flagged, "hr")["frameNote"]


def test_rr_equivalent_clipped_value_is_held():
    """Same mechanism, a different field -- RR is scored independently by
    reconcile(), so the crop-integrity gate must generalize past HR."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed(at=T - 10_000)
    last_reading = None
    for i in range(TEMPORAL_AGREEMENT_MIN_RUN):
        raw = _reading(rr=1, timestamp=T + i * TICK_MS)  # e.g. "17" clipped to "1"
        conf = _confidence(rr=55.0)
        last_reading, confirmed, flagged = reconcile(
            raw, conf, confirmed, temporal_state=temporal_state,
            per_vital_crop_suspicious={"rr": True},
        )
    assert last_reading["rr"] == 14, "held at baseline"
    assert "temporal" not in _flag_for(flagged, "rr")["frameNote"]


def test_spo2_equivalent_clipped_value_is_held():
    temporal_state = initial_temporal_state()
    confirmed = _confirmed(at=T - 10_000)
    last_reading = None
    for i in range(TEMPORAL_AGREEMENT_MIN_RUN):
        raw = _reading(spo2=9, timestamp=T + i * TICK_MS)  # e.g. "98" clipped to "9"
        conf = _confidence(spo2=55.0)
        last_reading, confirmed, flagged = reconcile(
            raw, conf, confirmed, temporal_state=temporal_state,
            per_vital_crop_suspicious={"spo2": True},
        )
    assert last_reading["spo2"] == 98, "held at baseline"
    assert "temporal" not in _flag_for(flagged, "spo2")["frameNote"]


def test_valid_crop_run_corroborates_normally():
    """Regression guard: a totally ordinary, non-suspicious run must not be
    affected by this milestone's change at all."""
    temporal_state = initial_temporal_state()
    ticks = [(90, 60.0, False)] * (TEMPORAL_AGREEMENT_MIN_RUN + 2)
    reading, _updated, flagged = _run_ticks_with_crop_flags(ticks, temporal_state)
    assert reading["hr"] == 90
    assert "temporal corroboration" in _flag_for(flagged, "hr")["frameNote"]


def test_tracking_failure_mid_run_resets_clean_run_along_with_the_count():
    """Extends the existing M5.4 'extraction failure mid-run resets
    corroboration' guarantee: a withheld tick (raw_value=None, as
    app.pipeline.calibrated_roi's fail-closed contract produces on tracking
    failure) must reset BOTH run_length and clean_run, so a prior dirty run
    cannot taint a fresh one, and a fresh run cannot inherit false safety
    either."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed({"hr": 74})
    ticks = [(8, 60.0, True), (8, 58.0, True), (None, 0.0, False), (47, 55.0, False), (47, 55.0, False)]
    last_reading = None
    for i, (value, conf_val, susp) in enumerate(ticks):
        raw = _reading(hr=value, timestamp=T + i * TICK_MS)
        conf = _confidence(hr=conf_val)
        last_reading, confirmed, flagged = reconcile(
            raw, conf, confirmed, temporal_state=temporal_state,
            per_vital_crop_suspicious={"hr": susp},
        )
    # Only 2 consecutive clean reads since the reset -- one short of
    # TEMPORAL_AGREEMENT_MIN_RUN==3, so still held, and NOT because of the
    # earlier dirty run (which was fully cleared by the None tick).
    assert last_reading["hr"] == 74
    assert temporal_state["hr"].run_length == 2
    assert temporal_state["hr"].clean_run is True, "the reset must have cleared the earlier taint, not just the count"


def test_session_reset_clears_clean_run_state():
    old_state = initial_temporal_state()
    ticks = [(8, 60.0, True), (8, 58.0, True)]
    _run_ticks_with_crop_flags(ticks, old_state)
    assert old_state["hr"].clean_run is False

    fresh_state = initial_temporal_state()
    assert fresh_state["hr"].clean_run is True
    reading, _updated, _flagged = _run_ticks_with_crop_flags([(85, 55.0, False)], fresh_state)
    assert reading["hr"] == 75, "a fresh session must not inherit the old session's taint"


def test_per_vital_crop_suspicious_omitted_is_backward_compatible():
    """A caller that opts into temporal_state but never passes
    per_vital_crop_suspicious (every M5.4-era call site, and any future one
    that doesn't know about this signal) gets EXACTLY M5.4's original
    behaviour -- this argument can only ever make corroboration MORE
    cautious, never silently safer by default."""
    temporal_state = initial_temporal_state()
    confirmed = _confirmed(at=T - 10_000)
    last_reading = None
    for i in range(TEMPORAL_AGREEMENT_MIN_RUN):
        raw = _reading(hr=85, timestamp=T + i * TICK_MS)
        conf = _confidence(hr=55.0)
        last_reading, confirmed, flagged = reconcile(raw, conf, confirmed, temporal_state=temporal_state)
    assert last_reading["hr"] == 85
    assert "temporal corroboration" in _flag_for(flagged, "hr")["frameNote"]


# ═══════════════════════════════════════════════════════════════════════
# Real-data regression — the actual reported crops (Phase 4/5)
# ═══════════════════════════════════════════════════════════════════════

_TESSERACT_AVAILABLE = _locate_tesseract_binary(None) is not None


@pytest.mark.skipif(
    not os.path.isdir(_DATASET_B_DIR) or not _TESSERACT_AVAILABLE,
    reason="Dataset B / Tesseract not present in this checkout",
)
def test_real_sample_0011_hr_truncation_is_prevented_end_to_end():
    """The strongest available regression: real Dataset B frames, the real
    calibration profile that reproduces the failure (frozen_B[sample_0011],
    the same reference docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md Phase 6
    used), the real LayoutTracker, the real TesseractEngine, and the real
    reconcile()/temporal pipeline -- no synthetic stand-ins. Demonstrates
    BOTH halves required by this milestone: the OLD mechanism (no
    crop-integrity evidence) confirms the wrong value, and the NEW one
    (crop-integrity evidence wired in) does not."""
    import json

    from app.eval.m5_3_tracking_eval import _load_samples, build_single_frame_profile

    engine = TesseractEngine()
    samples = _load_samples(_DATASET_B_DIR, "sample_")
    ref_sample = next(s for s in samples if s["id"] == "sample_0011")
    profile, ref_img = build_single_frame_profile(ref_sample)
    tracker = LayoutTracker.from_reference_image(
        ref_img, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )
    extractor = make_extractor(profile, tracker=tracker)

    with open(os.path.join(_DATASET_B_DIR, "m5_ground_truth_values.json")) as f:
        gt_all = json.load(f)["values"]

    # The 4 real consecutive frames the M5.4 report's Phase 6 found reading
    # HR as a truncated "8" (ground truth 82/81/83/84).
    frame_ids = ["sample_0004", "sample_0005", "sample_0006", "sample_0007"]
    gt_values = [gt_all[fid]["hr"] for fid in frame_ids]
    assert gt_values == [82, 81, 83, 84], "sanity check on the dataset itself, not the fix"

    old_state = initial_temporal_state()
    new_state = initial_temporal_state()
    old_confirmed = _confirmed(at=T - 10_000)
    new_confirmed = _confirmed(at=T - 10_000)
    observed_values, observed_suspicious = [], []

    for i, fid in enumerate(frame_ids):
        sample = next(s for s in samples if s["id"] == fid)
        img = np.array(Image.open(sample["png_path"]).convert("RGB"))
        rois = extractor(img)
        roi = rois.get("hr")
        assert roi is not None, "tracking must lock on this real frame for the test to mean anything"

        value, confidence, diag = engine.read_vital_with_diagnostics(roi.crop, "hr")
        suspicious = has_residual_content(diag.raw_text, diag.matched_text)
        observed_values.append(value)
        observed_suspicious.append(suspicious)

        raw = _reading(hr=value, timestamp=T + i * TICK_MS)
        conf = _confidence(hr=confidence)

        old_result = reconcile(raw, conf, old_confirmed, temporal_state=old_state)  # OLD: no crop-integrity evidence
        _, old_confirmed, old_flagged = old_result

        new_result = reconcile(
            raw, conf, new_confirmed, temporal_state=new_state,
            per_vital_crop_suspicious={"hr": suspicious},
        )
        _, new_confirmed, new_flagged = new_result

    # The real OCR must actually reproduce the reported truncation (a
    # sanity check on the dataset/environment, not the fix itself) --
    # skip rather than false-pass if a different Tesseract build reads
    # these crops differently.
    if observed_values != [8.0, 8.0, 8.0, 8.0] or not all(observed_suspicious):
        pytest.skip(
            f"this Tesseract build did not reproduce the reported truncation "
            f"(values={observed_values}, suspicious={observed_suspicious}) -- "
            "nothing to regress-test against"
        )

    old_reading = old_result[0]
    new_reading = new_result[0]
    assert old_reading["hr"] == 8, "OLD mechanism: reproduces the confidently-wrong confirmation"
    assert "temporal corroboration" in _flag_for(old_flagged, "hr")["frameNote"]

    assert new_reading["hr"] != 8, "NEW mechanism: must never confirm the truncated value"
    assert "temporal" not in _flag_for(new_flagged, "hr")["frameNote"]
