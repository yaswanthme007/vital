"""M5.8 regression tests -- one per root cause found while auditing the
real demo recording (docs/M5_8_REAL_CAMERA_OBSERVATION.md).

Every failure asserted here was OBSERVED, not hypothesised: the evidence is
the demo laptop's own `vital.db` (nine 1280x720 calibration reference frames
photographed off the physical anaesthesia monitor, plus 62 persisted camera
rows from session SESSION-1787247886294-cv6z) and the frozen Dataset A/B
crops this repo already carries.
"""

import asyncio
import os
from collections import deque

import numpy as np
import pytest
from PIL import Image

from app.alerts.rules import AlertThrottle
from app.pipeline.detect import detect_screen
from app.pipeline.ocr import (
    DOMINANT_ROW_HEIGHT_RATIO,
    OcrDiagnostics,
    TesseractEngine,
    _QUIET_ZONE_PAD,
    _SPARSE_CONFIG,
    _Token,
    _extract_tokens_with_boxes,
    _preprocess,
    dominant_row_tokens,
)
from app.pipeline.tier2_roi import extract_rois_by_field_classifier
from app.sources.base import Frame
from app.validation import live_corroboration as live
from app.validation.crop_integrity import crop_is_suspicious, has_residual_content
from app.validation.reconcile import DEFAULT_BASELINE, reconcile
from app.validation.rules import FIELDS
from app.ws.vitals import send_loop

DATASET_A_DIR = "app/eval/tier2_data/external_monitor_video"
_needs_dataset_a = pytest.mark.skipif(
    not os.path.isdir(DATASET_A_DIR), reason="Dataset A not present in this checkout"
)


# ─── 1. The dominant-row reader: alarm-limit labels never become the value ──


def _token(text, conf, left, top, width, height):
    return _Token(text=text, confidence=conf, left=left, top=top, width=width, height=height)


def test_alarm_limit_labels_are_excluded_from_the_reading():
    """THE root cause. An EtCO2 ROI drawn around the monitor's EtCO2 slot
    necessarily also contains that field's alarm limits ("65"/"25") and the
    inspired-CO2 readout ("4"), because the monitor draws them inside the
    slot. Before M5.8 every token was concatenated and the FIRST digit run
    won, which is how the live demo recorded EtCO2 = 4 and EtCO2 = 234 while
    the monitor plainly showed 34. Token heights here are the real measured
    ones from the demo laptop's own frame."""
    tokens = [
        _token("65", 91, 20, 10, 40, 19),   # alarm high limit
        _token("25", 96, 20, 32, 40, 19),   # alarm low limit
        _token("34", 96, 90, 5, 160, 82),   # THE reading
        _token("4", 93, 95, 120, 20, 37),   # inspired CO2
    ]
    assert [t.text for t in dominant_row_tokens(tokens)] == ["34"]


def test_rr_labels_are_excluded_so_12_never_reads_as_42():
    """The RR slot's own "30"/"8" alarm limits sit left of the reading; the
    old whole-crop reader spliced them into "42"/"420" for a monitor showing
    12 -- a value that reached the demo's Observation Ledger."""
    tokens = [
        _token("30", 96, 20, 8, 38, 19),
        _token("8", 96, 22, 30, 18, 19),
        _token("12", 96, 90, 4, 150, 83),
    ]
    assert [t.text for t in dominant_row_tokens(tokens)] == ["12"]


def test_a_value_split_across_two_tokens_is_kept_whole():
    """Tesseract sometimes returns a two-digit reading as two tokens. Both
    belong to the dominant row and both must survive -- dropping one is the
    truncation failure this project has already been bitten by twice."""
    tokens = [
        _token("100", 96, 20, 10, 40, 19),  # SpO2 alarm high limit
        _token("9", 95, 90, 5, 70, 98),
        _token("8", 95, 165, 5, 70, 98),
    ]
    assert [t.text for t in dominant_row_tokens(tokens)] == ["9", "8"]


def test_a_same_size_number_on_a_different_row_is_not_spliced_in():
    """Height alone is not enough: a loosely drawn box can catch a
    NEIGHBOURING field's equally large digits. The vertical-overlap term is
    what keeps them out."""
    tokens = [
        _token("88", 96, 90, 5, 150, 90),    # this field's reading
        _token("87", 96, 90, 200, 150, 90),  # the Pulse field, one row down
    ]
    assert [t.text for t in dominant_row_tokens(tokens)] == ["88"]


def test_the_anchor_is_always_a_digit_bearing_token():
    """A tall non-digit fragment (a waveform stroke Tesseract labelled "|",
    a unit label) must never define the row the value is read from."""
    tokens = [
        _token("|", 40, 10, 0, 6, 140),
        _token("36", 96, 90, 20, 150, 90),
    ]
    assert "36" in [t.text for t in dominant_row_tokens(tokens)]


def test_no_tokens_means_no_reading_not_a_guess():
    assert dominant_row_tokens([]) == []


def test_height_ratio_constant_sits_between_measured_label_and_glyph_sizes():
    """Guards the constant against drift in either direction. Measured on
    the real frames: label tokens are 0.1-0.35 of the primary glyph height,
    and a value's own tokens are within ~0.75 of each other."""
    assert 0.4 < DOMINANT_ROW_HEIGHT_RATIO < 0.75


# ─── 2. Crop integrity: two independent truncation signals ──────────────────


def test_textual_residual_still_flags_the_measured_truncation_signature():
    """M5.4.1's original evidence -- a clipped digit is recognized as a
    LETTER fused onto the digit run. M5.8 must not have weakened this."""
    assert has_residual_content("8g", "8") is True
    assert has_residual_content("8B", "8") is True


def test_symbol_noise_at_the_edges_is_not_treated_as_truncation():
    """Measured on the demo laptop's own frames: CORRECT reads come back as
    '"88', '\\?98', '#34', '* 150/80' -- the value plus one punctuation or
    unrecognized-glyph artifact from the waveform or a rule line touching
    the digits. Flagging those held every correct HR/SpO2/NIBP read on the
    real camera, which is a false hold with no safety benefit: a clipped
    digit has never been measured to OCR as punctuation."""
    for raw, matched in (('"88', "88"), ("\\?98", "98"), ("#34", "34"), ("* 150/80", "150/80"),
                         ("�91", "91"), ("98.6|", "98.6")):
        assert has_residual_content(raw, matched) is False, raw


def test_residual_in_the_middle_is_still_flagged():
    """Only the ENDS are stripped. Interior residue is untouched evidence."""
    assert has_residual_content("9A8", "9") is True


def test_geometric_signal_catches_a_truncation_the_textual_one_cannot():
    """Dataset A sample_0010's EtCO2 crop renders "34"; Tesseract's sparse
    pass tokenizes only the "4", at 96% confidence, with raw_text ==
    matched_text == "4". There is nothing textual to notice -- the missing
    "3" is only visible as ink the tokens do not account for."""
    diag = OcrDiagnostics(raw_text="4", matched_text="4", incomplete_row=True)
    assert has_residual_content(diag.raw_text, diag.matched_text) is False
    assert crop_is_suspicious(diag) is True


def test_crop_is_suspicious_reports_nothing_for_an_engine_without_evidence():
    """S11's OnnxDigitEngine returns a bare OcrDiagnostics. A caller that
    cannot derive integrity evidence must never guess in either direction."""
    assert crop_is_suspicious(OcrDiagnostics()) is False


@_needs_dataset_a
def test_dataset_a_sample_0010_truncation_is_flagged_end_to_end():
    """The same case as above, through the REAL pipeline on the REAL frame
    rather than a hand-built diagnostics object."""
    img = np.array(Image.open(os.path.join(DATASET_A_DIR, "sample_0010.png")).convert("RGB"))
    roi = extract_rois_by_field_classifier(detect_screen(img).image).get("etco2")
    assert roi is not None

    value, _confidence, diag = TesseractEngine().read_vital_with_diagnostics(roi.crop, "etco2")
    assert value == 4.0, "this crop's known truncated read"
    assert diag.incomplete_row is True
    assert crop_is_suspicious(diag) is True


@_needs_dataset_a
def test_a_clean_correct_read_is_not_flagged_incomplete():
    """The completeness check must not fire on a correctly-read crop -- a
    signal that flags everything protects nothing."""
    img = np.array(Image.open(os.path.join(DATASET_A_DIR, "sample_0046.png")).convert("RGB"))
    roi = extract_rois_by_field_classifier(detect_screen(img).image).get("etco2")
    assert roi is not None

    value, _confidence, diag = TesseractEngine().read_vital_with_diagnostics(roi.crop, "etco2")
    assert value == 34.0
    assert diag.incomplete_row is False


def test_dominant_row_completeness_ignores_short_label_ink():
    """Directly: the alarm-limit labels the dominant row deliberately
    excluded must not then be reported as 'unaccounted ink'."""
    from app.pipeline.ocr import _dominant_row_is_complete

    processed = np.full((200, 400), 255, dtype=np.uint8)
    processed[40:140, 150:300] = 0   # the reading, 100px tall
    processed[20:40, 20:60] = 0      # a label, 20px tall, far to the left
    selected = [_token("34", 96, 150, 40, 150, 100)]
    assert _dominant_row_is_complete(processed, selected) is True

    processed[40:140, 40:140] = 0    # a full-height digit the tokens missed
    assert _dominant_row_is_complete(processed, selected) is False


# ─── 3. Live corroboration: several frames, never one ───────────────────────


def _observe(evidence, *samples):
    for value, confidence, clean in samples:
        evidence.observe(value, confidence, not clean)
    return evidence


def test_one_frame_never_confirms_however_confident():
    evidence = _observe(live.FieldEvidence(), (98.0, 99.0, True))
    verdict = live.evaluate(evidence, 98.0)
    assert verdict.accepted is False
    assert verdict.reason == "awaiting_corroboration"


def test_two_clean_agreeing_frames_confirm():
    evidence = _observe(live.FieldEvidence(), (98.0, 96.0, True), (98.0, 95.0, True))
    verdict = live.evaluate(evidence, 98.0)
    assert verdict.accepted is True
    assert verdict.reason == "corroborated"


def test_three_agreeing_frames_confirm_at_the_lower_recovery_floor():
    """Real webcam OCR on a correct read routinely lands in the 45-65 band.
    Three agreeing clean frames is stronger evidence than one 70% frame --
    the same trade app.pipeline.burst_verify's recovery tier already makes,
    reusing its constant rather than inventing a new one."""
    evidence = _observe(live.FieldEvidence(), (89.0, 55.0, True), (89.0, 58.0, True), (89.0, 52.0, True))
    verdict = live.evaluate(evidence, 89.0)
    assert verdict.accepted is True
    assert verdict.reason == "corroborated_recovery"


def test_two_frames_below_the_full_floor_do_not_reach_the_recovery_tier():
    """The recovery tier requires MORE frames, not merely a lower bar."""
    evidence = _observe(live.FieldEvidence(), (89.0, 55.0, True), (89.0, 58.0, True))
    verdict = live.evaluate(evidence, 89.0)
    assert verdict.accepted is False
    assert verdict.reason == "low_confidence"


def test_a_transient_misread_between_agreeing_reads_is_rejected():
    """12 -> 42 -> 12: the 42 is the current tick, has one sample, and never
    confirms. This is verbatim the RR failure from the real demo."""
    evidence = _observe(live.FieldEvidence(), (12.0, 96.0, True), (42.0, 96.0, True))
    assert live.evaluate(evidence, 42.0).accepted is False


def test_a_real_change_is_detected_rather_than_smoothed_away():
    """CORRECTNESS > STABILITY. The window still holds three 88s, but the
    monitor now reads 90 and two frames agree on it -- 90 must win. An
    over-stabilized rule that required the window's MODE would hold 88."""
    evidence = _observe(
        live.FieldEvidence(),
        (88.0, 95.0, True), (88.0, 95.0, True), (88.0, 95.0, True),
        (90.0, 95.0, True), (90.0, 95.0, True),
    )
    verdict = live.evaluate(evidence, 90.0)
    assert verdict.accepted is True
    assert live.modal_value(evidence)[0] == 88.0, "the window's mode is deliberately NOT what decides"


def test_a_stale_majority_cannot_confirm_itself_once_the_display_moves_on():
    evidence = _observe(live.FieldEvidence(), (88.0, 95.0, True), (88.0, 95.0, True), (90.0, 95.0, True))
    assert live.evaluate(evidence, 90.0).accepted is False  # only one 90 so far
    assert live.evaluate(evidence, None).accepted is False


def test_a_systematically_truncated_crop_never_confirms_at_any_agreement():
    """The blind spot repetition alone cannot see: the SAME clipped box
    reads the SAME wrong value every frame, which looks exactly like
    agreement. Crop integrity is evaluated first and unconditionally."""
    evidence = _observe(
        live.FieldEvidence(),
        (8.0, 96.0, False), (8.0, 96.0, False), (8.0, 96.0, False), (8.0, 96.0, False), (8.0, 96.0, False),
    )
    verdict = live.evaluate(evidence, 8.0)
    assert verdict.accepted is False
    assert verdict.reason == "geometry"


def test_unreadable_frames_occupy_window_slots():
    """Two agreeing reads separated by several unreadable frames are not
    corroboration -- the misses are evidence too."""
    evidence = _observe(
        live.FieldEvidence(),
        (98.0, 96.0, True), (None, 0.0, True), (None, 0.0, True), (None, 0.0, True), (None, 0.0, True),
    )
    evidence.observe(98.0, 96.0, False)
    assert len(evidence.samples) == live.WINDOW_SIZE
    assert live.evaluate(evidence, 98.0).accepted is False


def test_window_is_bounded():
    evidence = live.FieldEvidence()
    for _ in range(200):
        evidence.observe(98.0, 96.0, False)
    assert len(evidence.samples) == live.WINDOW_SIZE


# ─── 4. reconcile(): no invented values on the camera path ──────────────────


def _camera_reconcile(raw_reading, confidence, confirmed, corroboration, status):
    return reconcile(
        raw_reading, confidence, confirmed, field_status=status,
        corroboration=corroboration, allow_baseline=False,
    )


def test_camera_path_reports_null_not_a_baseline_for_a_never_confirmed_field():
    """THE defect this milestone exists to remove. The live workspace showed
    HR 75, Temp 36.8 and RR 14 -- DEFAULT_BASELINE, verbatim -- captioned
    "Held - last confirmed 22:51:42", for a monitor displaying 89, 98.6F and
    12. Nothing the camera has not read may appear on a card."""
    status = {}
    reading, confirmed, _flagged = _camera_reconcile(
        {f: None for f in FIELDS} | {"timestamp": 1_700_000_000_000},
        {}, {}, live.initial_evidence_state(), status,
    )
    for field in FIELDS:
        assert reading[field] is None, field
        assert status[field] == "unknown", field
        assert reading[field] != DEFAULT_BASELINE[field]
    assert confirmed == {}, "an unconfirmed field must not enter last_confirmed either"


def test_camera_path_stays_unknown_rather_than_laundering_a_baseline_into_held():
    """The specific mechanism: pre-M5.8 the first tick wrote DEFAULT_BASELINE
    into last_confirmed, so the SECOND tick found a prior value and reported
    'held' -- a fabricated number, permanently labelled as an observation
    that had been confirmed at a real timestamp."""
    corroboration = live.initial_evidence_state()
    confirmed = {}
    for tick in range(3):
        status = {}
        reading, confirmed, _flagged = _camera_reconcile(
            {f: None for f in FIELDS} | {"timestamp": 1_700_000_000_000 + tick * 1000},
            {}, confirmed, corroboration, status,
        )
        assert status["hr"] == "unknown"
        assert reading["hr"] is None


def test_non_camera_sources_keep_their_baseline_behaviour():
    """Synthetic/replay sources are not claiming to observe a physical
    monitor, and frozen milestone evidence asserts on their exact shape."""
    from app.validation.reconcile import initial_confirmed_state

    status = {}
    reading, _confirmed, _flagged = reconcile(
        {f: None for f in FIELDS} | {"timestamp": 1_700_000_000_000},
        {}, initial_confirmed_state(1_700_000_000_000), field_status=status,
    )
    assert reading["hr"] == DEFAULT_BASELINE["hr"]
    assert status["hr"] == "held"


def test_range_and_jump_checks_still_run_before_corroboration():
    """Corroboration ADDS a requirement; it never bypasses an existing one.
    A physiologically impossible value repeated on every frame stays out."""
    corroboration = live.initial_evidence_state()
    confirmed = {}
    status = {}
    for tick in range(5):
        reading, confirmed, _flagged = _camera_reconcile(
            {f: None for f in FIELDS} | {"spo2": 3.0, "timestamp": 1_700_000_000_000 + tick * 1000},
            {"spo2": 99.0}, confirmed, corroboration, status,
        )
    assert status["spo2"] != "confirmed"
    assert reading["spo2"] is None


def test_routine_holds_do_not_generate_flagged_review_rows_on_the_camera_path():
    """The real demo produced 4,374 FlaggedReading rows in four minutes,
    essentially all of them "OCR confidence below threshold" -- burying the
    handful of genuinely reviewable events and making Archive's flagged
    count meaningless."""
    status = {}
    _reading, _confirmed, flagged = _camera_reconcile(
        {f: None for f in FIELDS} | {"timestamp": 1_700_000_000_000},
        {}, {}, live.initial_evidence_state(), status,
    )
    assert flagged == []


def test_an_implausible_value_is_still_flagged_for_review():
    """The suppression above is narrow: a rejected range/jump is a real
    event and must still reach the review queue."""
    status = {}
    _reading, _confirmed, flagged = _camera_reconcile(
        {f: None for f in FIELDS} | {"spo2": 3.0, "timestamp": 1_700_000_000_000},
        {"spo2": 99.0}, {}, live.initial_evidence_state(), status,
    )
    assert [f["vital"] for f in flagged] == ["spo2"]
    assert flagged[0]["severity"] == "critical"


# ─── 5. End to end through send_loop: calibration is not ground truth ───────


class _FrameList:
    def __init__(self, frames):
        self.frames = frames

    async def stream(self):
        for frame in self.frames:
            yield frame


class _Sink:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def _camera_frame(hr, t, confidence=95.0):
    reading = {f: None for f in FIELDS}
    reading["hr"] = hr
    reading["timestamp"] = t
    return Frame(
        reading=reading,
        per_vital_confidence={"hr": confidence, "spo2": 0.0, "nibp": 0.0, "etco2": 0.0, "temp": 0.0, "rr": 0.0},
        provenance="ai_high",
    )


def test_live_camera_corrects_a_wrong_starting_value():
    """CALIBRATION IS NOT GROUND TRUTH. Calibration confirmed HR = 90; the
    monitor actually shows 89. The live path must converge on 89 and keep
    it, not stay pinned to the value the operator confirmed at setup.

    Modelled here at the layer that decides: the session opens with 90
    already confirmed (whatever put it there), then the camera reads 89."""
    T = 1_700_000_000_000
    sink = _Sink()
    frames = [_camera_frame(90, T)] + [_camera_frame(89, T + i * 1000) for i in range(1, 6)]

    asyncio.run(send_loop(
        sink.send_json, _FrameList(frames), AlertThrottle(), deque(maxlen=10), source_tag="camera",
    ))

    hr_values = [m["reading"]["hr"] for m in sink.messages if m["type"] == "reading"]
    assert hr_values[-1] == 89, hr_values
    statuses = [m["fieldStatus"]["hr"] for m in sink.messages if m["type"] == "reading"]
    assert statuses[-1] == "confirmed"


def test_live_camera_holds_the_last_confirmed_value_when_reading_fails():
    """A held value stays VISIBLE (no flicker to blank) but is honestly
    labelled, and produces no new observation."""
    T = 1_700_000_000_000
    sink = _Sink()
    frames = [_camera_frame(89, T), _camera_frame(89, T + 1000), _camera_frame(None, T + 2000)]

    asyncio.run(send_loop(
        sink.send_json, _FrameList(frames), AlertThrottle(), deque(maxlen=10), source_tag="camera",
    ))

    readings = [m for m in sink.messages if m["type"] == "reading"]
    assert readings[-1]["reading"]["hr"] == 89
    assert readings[-1]["fieldStatus"]["hr"] == "held"


def test_recovery_tier_flags_a_changed_value_once_not_every_tick():
    """A value confirmed through the recovery tier (strong agreement, modest
    per-frame confidence) IS worth one review item -- but only when it
    genuinely changes. Re-confirming the SAME number every tick for hours is
    the review-queue flooding _ROUTINE_HOLD_REASONS exists to stop, arriving
    through the accept branch instead of the hold branch: measured at 36
    FlaggedReading rows across 14 frames of the real monitor photograph
    before this was fixed."""
    corroboration = live.initial_evidence_state()
    confirmed = {}
    flags_per_tick = []
    for tick in range(8):
        status = {}
        _reading, confirmed, flagged = _camera_reconcile(
            {f: None for f in FIELDS} | {"hr": 88.0, "timestamp": 1_700_000_000_000 + tick * 1000},
            {"hr": 55.0},  # below CONFIDENCE_MEDIUM_MIN -> the recovery tier
            confirmed, corroboration, status,
        )
        flags_per_tick.append([f["vital"] for f in flagged])

    assert status["hr"] == "confirmed", "the recovery tier must still confirm this value"
    hr_flag_ticks = [i for i, flags in enumerate(flags_per_tick) if "hr" in flags]
    assert len(hr_flag_ticks) == 1, f"expected exactly one review item, got ticks {hr_flag_ticks}"


def test_recovery_tier_flags_again_when_the_value_actually_changes():
    """The suppression above must not silence a genuinely NEW observation."""
    corroboration = live.initial_evidence_state()
    confirmed = {}
    seen = []
    for value in (88.0, 88.0, 88.0, 88.0, 90.0, 90.0, 90.0, 90.0):
        status = {}
        _reading, confirmed, flagged = _camera_reconcile(
            {f: None for f in FIELDS} | {"hr": value, "timestamp": 1_700_000_000_000 + len(seen) * 1000},
            {"hr": 55.0}, confirmed, corroboration, status,
        )
        seen.append(([f["vital"] for f in flagged], status.get("hr")))

    flagged_ticks = [i for i, (flags, _s) in enumerate(seen) if "hr" in flags]
    assert len(flagged_ticks) == 2, f"expected one review item per distinct value, got {flagged_ticks}"
