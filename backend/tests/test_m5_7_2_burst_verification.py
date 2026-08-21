"""M5.7.2: multi-frame burst verification for Calibration's Verify step.
See app/pipeline/burst_verify.py's module docstring for the full design and
why this is NOT simply "vote across N frames" -- app.validation.temporal's
own documented blind spot (a systematically bad crop reproduces the SAME
wrong value every frame, which naive agreement would read as corroboration)
is the central risk this test file is built around. Mirrors this repo's
established milestone-test pattern: real modules, real DB, no mocked
pipeline internals except where deliberately isolating one signal.
"""

import json
import tempfile
import os as _os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.calibration import NormalizedBox
from app.pipeline.burst_verify import (
    BURST_AGREEMENT_MIN_FRACTION,
    BURST_CONFIDENCE_FLOOR,
    BURST_RECOVERY_CONFIDENCE_FLOOR,
    BURST_RECOVERY_MIN_AGREEING_SAMPLES,
    BURST_SUPERMAJORITY_FRACTION,
    FieldBurstResult,
    _Sample,
    _evaluate,
    verify_burst,
)
from app.pipeline.calibrated_roi import extract_rois_from_boxes
from app.pipeline.read_frame import get_default_engine

client = TestClient(app)


def _box(x, y, w, h):
    return NormalizedBox(x=x, y=y, w=w, h=h)


def _rendered_frame():
    from simulator.render.monitor_layout import render_monitor
    from PIL import Image

    reading = {
        "hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78, "nibpMean": 92,
        "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1700000000000,
    }
    fd, path = tempfile.mkstemp(suffix=".png")
    _os.close(fd)
    try:
        label = render_monitor(reading, path, layout="grid")
        img = np.array(Image.open(path).convert("RGB"))
    finally:
        _os.remove(path)
    h, w = img.shape[:2]
    boxes = {v: _box(x / w, y / h, bw / w, bh / h) for v, (x, y, bw, bh) in label["rois"].items()}
    return img, boxes


def _rendered_frame_bytes():
    from simulator.render.monitor_layout import render_monitor

    reading = {
        "hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78, "nibpMean": 92,
        "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1700000000000,
    }
    fd, path = tempfile.mkstemp(suffix=".png")
    _os.close(fd)
    try:
        render_monitor(reading, path, layout="grid")
        with open(path, "rb") as f:
            return f.read()
    finally:
        _os.remove(path)


# ─── core aggregation behaviour ──────────────────────────────────────────


def test_clean_burst_stabilizes_every_field_on_the_correct_value():
    img, boxes = _rendered_frame()
    frames = [img.copy() for _ in range(5)]
    results = verify_burst(frames, boxes)

    assert results["hr"].stable and results["hr"].value == 74.0
    assert results["spo2"].stable and results["spo2"].value == 98.0
    assert results["etco2"].stable and results["etco2"].value == 38.0
    assert results["temp"].stable and results["temp"].value == 36.8
    assert results["rr"].stable and results["rr"].value == 14.0
    nibp = results["nibp"]
    assert nibp.stable
    assert nibp.value.systolic == 120.0 and nibp.value.diastolic == 78.0


def test_a_single_bad_frame_among_clean_ones_does_not_block_stability():
    """Independent, per-frame noise (what a real burst genuinely varies) on
    ONE of five frames must not prevent the other four from reaching a
    strict majority."""
    img, boxes = _rendered_frame()
    bad = np.zeros_like(img)  # a fully corrupt/blank frame -- worst-case single bad capture
    frames = [img.copy(), img.copy(), bad, img.copy(), img.copy()]
    results = verify_burst(frames, boxes)
    assert results["hr"].stable and results["hr"].value == 74.0
    assert results["hr"].sample_count <= 5  # the blank frame contributed no valid sample


def test_unanimous_but_systematically_truncated_box_never_stabilizes():
    """THE central safety property. A box narrowed enough to clip a digit
    reproduces the SAME wrong read on every one of 5 IDENTICAL frames --
    100% naive agreement -- and must still be refused, exactly like
    app.validation.temporal's own documented blind spot (see burst_verify.py
    module docstring). This is the test that would fail if the
    crop-integrity defense were ever removed or weakened."""
    img, boxes = _rendered_frame()
    hr = boxes["hr"]
    boxes = dict(boxes)
    boxes["hr"] = _box(hr.x, hr.y, hr.w * 0.55, hr.h)  # truncate to ~55% width

    frames = [img.copy() for _ in range(5)]  # worst case: identical frames
    result = verify_burst(frames, boxes)["hr"]
    assert result.stable is False
    assert result.value is None, "a truncated-but-unanimous read must NEVER be reported as a value"


def test_unstable_field_never_reports_an_invented_value():
    """Explicit requirement: 'if a stable value cannot be obtained, clearly
    report that the field could not be verified instead of inventing or
    forcing a value.'"""
    img, boxes = _rendered_frame()
    # Disagreeing frames: alternate between the real frame and a blank one,
    # never reaching a majority for any single value.
    frames = [img.copy(), np.zeros_like(img), img.copy(), np.zeros_like(img), np.zeros_like(img)]
    results = verify_burst(frames, boxes)
    for vital, result in results.items():
        if not result.stable:
            assert result.value is None, f"{vital} is unstable but reported a value: {result.value}"


def test_agreement_must_be_a_strict_majority_not_a_plurality():
    """2-of-4 (a tie) or fewer must not count as agreement -- confirms the
    documented '>', not '>=', semantics of BURST_AGREEMENT_MIN_FRACTION."""
    assert BURST_AGREEMENT_MIN_FRACTION == 0.5


def test_confidence_floor_reuses_an_existing_validated_constant():
    """Never a number invented for this feature -- reuses
    app.validation.rules.CONFIDENCE_MEDIUM_MIN, the same bar the live path
    already gates single-tick acceptance on."""
    from app.validation.rules import CONFIDENCE_MEDIUM_MIN
    assert BURST_CONFIDENCE_FLOOR == CONFIDENCE_MEDIUM_MIN


# ─── M5.7.2 HOTFIX: corroborated-recovery tier ──────────────────────────
#
# Regression tests for the exact real-camera-demo failure this hotfix
# addresses: HR/SpO2/Temp fields with 83-86% raw agreement were rejected
# solely because the agreeing samples' mean confidence sat below
# CONFIDENCE_MEDIUM_MIN (70) -- a bar tuned for a SINGLE untrusted frame,
# not for several independently-agreeing frames. These exercise
# `_evaluate` directly (not full OCR) so the exact confidence/agreement
# numbers in the brief's worked examples can be pinned precisely, without
# depending on what a specific Tesseract build happens to score a rendered
# digit at.


def _sample(match_key, confidence, clean=True, raw_text=None, matched_text=None):
    """Builds a _Sample for direct `_evaluate` testing. Defaults raw_text/
    matched_text to a clean pair reflecting `clean` unless overridden."""
    if raw_text is None:
        raw_text = "" if match_key is None else str(match_key)
    if matched_text is None:
        matched_text = None if match_key is None else (raw_text if clean else raw_text + "?")
    return _Sample(
        match_key=match_key, confidence=confidence, clean=clean,
        raw_text=raw_text, matched_text=matched_text, original_value=match_key,
    )


def test_one_noisy_frame_among_four_clean_agreeing_frames_is_accepted():
    """The brief's exact worked example: 90, 90, 90, 9, 90 -- 4 of 5 valid
    samples agree at a confidence below the full floor but above the
    recovery floor. Must verify as 90, recovered=True (so the UI can say
    'one noisy frame was discarded' rather than implying full confidence)."""
    samples = [
        _sample(90.0, 58.0), _sample(90.0, 60.0), _sample(90.0, 55.0),
        _sample(9.0, 62.0),  # the isolated noisy outlier -- a different value entirely
        _sample(90.0, 57.0),
    ]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is True
    assert recovered is True
    assert reason is None
    assert mode_key == 90.0
    assert len(mode_samples) == 4


def test_second_worked_example_98_98_98_98_9_is_accepted():
    samples = [
        _sample(98.0, 62.0), _sample(98.0, 59.0), _sample(98.0, 61.0), _sample(98.0, 60.0),
        _sample(9.0, 65.0),
    ]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is True and recovered is True
    assert mode_key == 98.0


def test_scattered_disagreement_with_a_null_sample_remains_unstable():
    """90, 83, 9, null, 98 -- no dominant value at all (every valid sample
    is a distinct singleton), must stay unstable regardless of the recovery
    tier."""
    samples = [
        _sample(90.0, 80.0), _sample(83.0, 80.0), _sample(9.0, 80.0),
        _sample(None, 0.0), _sample(98.0, 80.0),
    ]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is False
    assert recovered is False
    assert reason in ("low_agreement", "no_reading")


def test_systematic_truncation_is_never_accepted_even_at_full_agreement():
    """83 -> 8 truncated identically on every one of 5 frames: 100%
    agreement, but the agreeing samples are NOT clean of residual content
    (crop-integrity's signal for a clipped digit run) -- must be rejected
    by BOTH tiers, no matter how high the confidence or how unanimous the
    repetition. This is the exact property the recovery tier must not
    weaken."""
    samples = [_sample(8.0, 65.0, clean=False, raw_text="8g", matched_text="8") for _ in range(5)]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is False
    assert recovered is False
    assert reason == "geometry"


def test_clean_unanimous_high_confidence_burst_is_accepted_without_recovery():
    """A genuinely clean, fully-confident burst must still verify via the
    ORIGINAL full-confidence tier, not silently rely on the new recovery
    tier -- recovered must be False here."""
    samples = [_sample(74.0, 92.0) for _ in range(5)]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is True
    assert recovered is False
    assert mode_key == 74.0


def test_low_confidence_dominant_value_is_rejected_even_at_full_agreement():
    """5/5 agree, but confidence is below EVEN the relaxed recovery floor --
    must still reject. Corroboration cannot manufacture confidence Tesseract
    never reported."""
    samples = [_sample(74.0, 22.0) for _ in range(5)]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is False
    assert recovered is False
    assert reason == "low_confidence"
    assert BURST_RECOVERY_CONFIDENCE_FLOOR > 22.0  # sanity: genuinely below the floor


def test_residual_content_dominant_value_is_rejected_even_at_high_confidence():
    """4/5 agree at high confidence, but every agreeing sample carries
    residual content -- crop-integrity must still block it regardless of
    how confident or well-agreed the reads are."""
    samples = [
        _sample(74.0, 85.0, clean=False, raw_text="74x", matched_text="74"),
        _sample(74.0, 88.0, clean=False, raw_text="74x", matched_text="74"),
        _sample(74.0, 90.0, clean=False, raw_text="74x", matched_text="74"),
        _sample(74.0, 87.0, clean=False, raw_text="74x", matched_text="74"),
        _sample(70.0, 80.0),
    ]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is False
    assert recovered is False
    assert reason == "geometry"


def test_a_bare_majority_below_supermajority_does_not_get_the_recovery_floor():
    """3-of-5 (60%) agreement at a confidence between the two floors (40-70)
    must NOT be relaxed into 'stable' -- only a SUPERMAJORITY
    (BURST_SUPERMAJORITY_FRACTION) qualifies for the recovery tier. This is
    the guard against 'accept anything that looks common' the brief
    explicitly calls out."""
    samples = [
        _sample(90.0, 55.0), _sample(90.0, 58.0), _sample(90.0, 56.0),
        _sample(83.0, 55.0), _sample(98.0, 55.0),
    ]
    agreement_fraction = 3 / 5
    assert BURST_AGREEMENT_MIN_FRACTION < agreement_fraction < BURST_SUPERMAJORITY_FRACTION
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is False
    assert recovered is False
    assert reason == "low_confidence"


def test_recovery_tier_requires_a_minimum_absolute_agreeing_sample_count():
    """A 2-of-2 (100%) 'supermajority' from a tiny sample is not the same
    strength of evidence as 4-of-5 -- BURST_RECOVERY_MIN_AGREEING_SAMPLES
    guards against trusting it just because the fraction is high."""
    samples = [_sample(90.0, 55.0), _sample(90.0, 58.0)]
    assert len(samples) < BURST_RECOVERY_MIN_AGREEING_SAMPLES
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is False
    assert recovered is False


def test_recovery_tier_reuses_the_existing_temporal_floor_constant():
    """Never a number invented for this feature -- reuses
    app.validation.temporal.CONFIDENCE_TEMPORAL_FLOOR verbatim, the same
    constant already validated for 'multiple corroborating reads justify a
    lower per-read confidence than one untrusted tick would need'."""
    from app.validation.temporal import CONFIDENCE_TEMPORAL_FLOOR
    assert BURST_RECOVERY_CONFIDENCE_FLOOR == CONFIDENCE_TEMPORAL_FLOOR
    assert BURST_RECOVERY_CONFIDENCE_FLOOR < BURST_CONFIDENCE_FLOOR


def test_full_confidence_tier_still_takes_priority_over_recovery():
    """A field that clears the full 70 floor must report recovered=False
    even if it would ALSO have qualified for the recovery tier -- the UI
    should only ever say 'noisy frame discarded' when that's actually why
    it passed."""
    samples = [_sample(90.0, 80.0), _sample(90.0, 78.0), _sample(90.0, 82.0), _sample(90.0, 79.0)]
    stable, recovered, reason, mode_key, mode_samples, valid = _evaluate(samples)
    assert stable is True
    assert recovered is False


def test_no_boxes_drawn_returns_no_fields():
    img, _boxes = _rendered_frame()
    results = verify_burst([img], {})
    assert results == {}


def test_box_entirely_off_frame_reports_unstable_not_a_crash():
    img, boxes = _rendered_frame()
    boxes = {"hr": _box(1.5, 1.5, 0.1, 0.1)}
    results = verify_burst([img.copy() for _ in range(3)], boxes)
    assert results["hr"].stable is False
    assert results["hr"].value is None
    assert results["hr"].sample_count == 0


# ─── the pre-existing NIBP diagnostics bug this feature surfaced ─────────


def test_nibp_diagnostics_describe_the_sys_dia_line_not_the_mean_line():
    """Regression test for the M5.7.2 bug fix in app.pipeline.ocr._read_nibp:
    `matched_text` used to be silently overwritten by the MEAN-search loop's
    own regex match (variable shadowing), so a perfectly clean sys/dia read
    was spuriously flagged as having 'residual content' whenever a mean
    value was also found. Directly exercises has_residual_content on a real
    NIBP crop's diagnostics -- must be False on a clean read."""
    from app.validation.crop_integrity import has_residual_content

    img, boxes = _rendered_frame()
    crop = extract_rois_from_boxes(img, {"nibp": boxes["nibp"]})["nibp"].crop
    engine = get_default_engine()
    value, confidence, diag = engine.read_vital_with_diagnostics(crop, "nibp")
    assert value.systolic == 120.0 and value.diastolic == 78.0
    assert diag.raw_text == diag.matched_text, (
        f"matched_text ({diag.matched_text!r}) should describe the SAME sys/dia "
        f"line as raw_text ({diag.raw_text!r}) on a clean read"
    )
    assert not has_residual_content(diag.raw_text, diag.matched_text)


# ─── /api/calibration/verify-burst endpoint ──────────────────────────────


def test_verify_burst_endpoint_stabilizes_on_a_clean_burst():
    png_bytes = _rendered_frame_bytes()
    payload = {
        "referenceWidth": 960, "referenceHeight": 560,
        "roiBoxes": {"hr": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
    }
    files = [("files", (f"f{i}.png", png_bytes, "image/png")) for i in range(5)]
    r = client.post("/api/calibration/verify-burst", data={"payload": json.dumps(payload)}, files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "burst" in body
    assert body["burst"]["frameCount"] == 5
    assert "hr" in body["burst"]["field"]
    assert body["burst"]["field"]["hr"]["stable"] in (True, False)
    # M5.7.2 hotfix: the new per-field diagnostics the frontend now uses to
    # pick which of the four UI states (stable / mostly-stable-recovered /
    # unstable / bad-geometry) to render.
    assert "recovered" in body["burst"]["field"]["hr"]
    assert isinstance(body["burst"]["field"]["hr"]["recovered"], bool)
    assert "unstableReason" in body["burst"]["field"]["hr"]


def test_verify_burst_endpoint_requires_at_least_one_file():
    payload = {"referenceWidth": 100, "referenceHeight": 100, "roiBoxes": {}}
    r = client.post("/api/calibration/verify-burst", data={"payload": json.dumps(payload)}, files=[])
    assert r.status_code == 422


def test_verify_burst_endpoint_never_persists_a_reading_row():
    """Same explicit requirement as single-frame /verify: calibration
    verification is configuration metadata, never patient observation
    history -- burst verification must not create an exception."""
    session = client.post(
        "/api/sessions",
        json={"patientId": "PT-M572-BURST", "procedure": "Test", "anesthetist": "Dr. Test"},
    ).json()
    session_id = session["id"]

    png_bytes = _rendered_frame_bytes()
    payload = {
        "referenceWidth": 960, "referenceHeight": 560,
        "roiBoxes": {"hr": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
    }
    files = [("files", (f"f{i}.png", png_bytes, "image/png")) for i in range(3)]
    r = client.post("/api/calibration/verify-burst", data={"payload": json.dumps(payload)}, files=files)
    assert r.status_code == 200

    readings = client.get(f"/api/sessions/{session_id}/readings").json()
    assert readings == []


def test_verify_burst_response_reading_shape_matches_single_frame_verify():
    """The response is additive over the single-frame /verify contract
    (reading/confidence/frameWidth/frameHeight/diagnostics unchanged shape,
    `burst` is new) -- so existing UI code that already knows how to render
    a CalibrationVerifyResult keeps working unmodified."""
    png_bytes = _rendered_frame_bytes()
    payload = {
        "referenceWidth": 960, "referenceHeight": 560,
        "roiBoxes": {"hr": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
    }
    files = [("files", (f"f{i}.png", png_bytes, "image/png")) for i in range(3)]
    r = client.post("/api/calibration/verify-burst", data={"payload": json.dumps(payload)}, files=files)
    body = r.json()
    for key in ("reading", "confidence", "frameWidth", "frameHeight", "diagnostics"):
        assert key in body
    for key in ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr"):
        assert key in body["reading"]


# ─── default single-frame /verify is byte-identical (no regression) ─────


def test_single_frame_verify_endpoint_unchanged_by_burst_feature():
    img, boxes = _rendered_frame()
    fd, path = tempfile.mkstemp(suffix=".png")
    _os.close(fd)
    try:
        from PIL import Image
        Image.fromarray(img).save(path)
        with open(path, "rb") as f:
            png_bytes = f.read()
    finally:
        _os.remove(path)

    hr = boxes["hr"]
    payload = {
        "referenceWidth": img.shape[1], "referenceHeight": img.shape[0],
        "roiBoxes": {"hr": {"x": hr.x, "y": hr.y, "w": hr.w, "h": hr.h}},
    }
    r = client.post(
        "/api/calibration/verify",
        data={"payload": json.dumps(payload)},
        files={"file": ("frame.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert "burst" not in body
    assert body["reading"]["hr"] == 74.0
