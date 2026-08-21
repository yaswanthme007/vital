"""M3 Phase 8, items 6-14: end-to-end Tier-2 integration through
read_frame(), reconcile(), CameraSource, and the WebSocket path. Real ONNX
FieldCNN inference and real held-out-TEST external-monitor images throughout
-- not mocks -- per the M3 task's instruction. Skipped (not failed) on a
machine without the trained field classifier.
"""

import asyncio
import io
import json
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.pipeline.field_classifier import model_available
from app.pipeline.ocr import NibpValue, OcrEngine
from app.pipeline.read_frame import VITALS, read_frame
from app.pipeline.roi import extract_rois_by_colour
from app.pipeline.tier2_roi import extract_rois_by_field_classifier
from app.sources import frame_queue
from app.sources.camera import CameraSource
from app.validation.reconcile import initial_confirmed_state, reconcile
from app.validation.rules import is_in_range

pytestmark = pytest.mark.skipif(not model_available(), reason="No trained Tier-2 field classifier present")

DATASET_DIR = os.path.join("app", "eval", "tier2_data", "external_monitor_video")
CLIENT = TestClient(app)


def _real_image_bytes(sample_id: str) -> bytes:
    with open(os.path.join(DATASET_DIR, f"{sample_id}.png"), "rb") as f:
        return f.read()


def _real_image_array(sample_id: str) -> np.ndarray:
    return np.array(Image.open(os.path.join(DATASET_DIR, f"{sample_id}.png")).convert("RGB"))


class _SpyEngine(OcrEngine):
    """Records every crop it's asked to read, then returns a fixed value --
    lets a test prove read_frame() handed the classifier-selected crop
    (not the raw candidate, not the whole frame) to OCR, without depending
    on Tesseract actually being installed on the test machine."""

    def __init__(self):
        self.calls = []

    def read_vital(self, crop, vital_type):
        self.calls.append((vital_type, crop.shape))
        if vital_type == "nibp":
            return NibpValue(120.0, 80.0, 92.0), 95.0
        return 42.0, 95.0


class _FailingEngine(OcrEngine):
    """Always reports OCR failure, exactly the way TesseractEngine's own
    contract requires (return None/all-None, never raise) -- proves
    read_frame()/Tier-2 doesn't crash or fabricate a value when OCR fails
    on a classifier-selected crop."""

    def read_vital(self, crop, vital_type):
        if vital_type == "nibp":
            return NibpValue(None, None, None), 0.0
        return None, 0.0


# ─── Tier-1 unchanged ────────────────────────────────────────────────────


def test_tier1_mode_unaffected_by_tier2_roi_extractor_existing(monkeypatch):
    """ROI_ENGINE unset (or 'tesseract') must still resolve to the exact
    same extract_rois_by_colour function object -- not a wrapper, not a
    behavioural near-match."""
    monkeypatch.delenv("ROI_ENGINE", raising=False)
    import app.pipeline.read_frame as read_frame_module

    read_frame_module._default_roi_extractor = None
    assert read_frame_module.get_default_roi_extractor() is extract_rois_by_colour


# ─── Tier-2 invokes FieldCNN + selected-crop OCR integration ──────────────


def test_tier2_roi_extractor_used_when_explicitly_passed():
    """Bypasses the env var entirely (roi_extractor is an explicit
    read_frame() parameter, per Phase 6's swap-point design) -- confirms
    read_frame() actually calls the Tier-2 stage, not just that the
    selector function resolves correctly (already covered in
    test_roi_engine_selection.py)."""
    img = _real_image_array("sample_0017")
    spy = _SpyEngine()

    reading, confidences = read_frame(img, engine=spy, roi_extractor=extract_rois_by_field_classifier)

    assert set(reading.keys()) == {
        "hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr",
    }
    assert set(confidences.keys()) == set(VITALS)
    # sample_0017 is M2's "clean pass" frame -- Tier-2 should hand OCR at
    # least a couple of real crops on it, proving the FieldCNN path actually
    # ran and selected candidates rather than finding nothing.
    assert len(spy.calls) >= 2


def test_ocr_is_called_on_the_classifier_selected_crop_not_the_raw_candidate():
    """The crop shape OCR receives must match the box tier2_roi itself
    resolved for that vital -- proving the SAME selected region flows
    downstream, not some other candidate or the whole frame."""
    img = _real_image_array("sample_0017")
    expected = extract_rois_by_field_classifier(img)
    spy = _SpyEngine()

    read_frame(img, engine=spy, roi_extractor=extract_rois_by_field_classifier)

    called_shapes = {vital: shape for vital, shape in spy.calls}
    for vital, roi_result in expected.items():
        if roi_result is None:
            assert vital not in called_shapes
        else:
            assert called_shapes[vital] == roi_result.crop.shape


def test_multiple_candidates_for_same_vital_resolved_deterministically():
    """Running the full Tier-2 stage twice on the same real frame must give
    the exact same per-vital selection every time -- no nondeterminism from
    dict/set ordering, ONNX runtime threading, or anything else."""
    img = _real_image_array("sample_0035")
    r1 = extract_rois_by_field_classifier(img)
    r2 = extract_rois_by_field_classifier(img)
    for vital in VITALS:
        b1, b2 = r1[vital], r2[vital]
        if b1 is None:
            assert b2 is None
        else:
            assert b2 is not None
            assert b1.box == b2.box
            assert b1.classifier_confidence == b2.classifier_confidence


# ─── Safety: low confidence / OCR failure never become a trusted reading ──


def test_low_classifier_confidence_never_becomes_a_trusted_reading(monkeypatch):
    """Forcing the confidence floor above what any real candidate can reach
    must drive every vital to 'not found' -- read_frame() then reports 0.0
    confidence and a None value, the same safe path a genuine Tier-1 miss
    already takes (TIER2_RECOGNITION_SPIKE.md sec 11)."""
    import app.pipeline.tier2_roi as tier2_roi_module

    # Softmax can legitimately land within a hair of 1.0 on a clean crop, so
    # 0.999 alone isn't guaranteed to clear every real candidate -- 1.5 is
    # above the maximum any probability can ever be, guaranteeing the floor
    # is genuinely unreachable rather than just "very high".
    monkeypatch.setattr(tier2_roi_module, "MIN_CLASSIFIER_CONFIDENCE", 1.5)

    img = _real_image_array("sample_0017")
    spy = _SpyEngine()
    reading, confidences = read_frame(img, engine=spy, roi_extractor=extract_rois_by_field_classifier)

    assert len(spy.calls) == 0
    assert all(v == 0.0 for v in confidences.values())
    assert all(reading[f] is None for f in ("hr", "spo2", "etco2", "temp", "rr"))
    assert reading["nibpSystolic"] is None


def test_ocr_failure_on_a_tier2_selected_crop_is_handled_safely():
    """OCR reporting failure (None/0.0, its documented contract) on a
    classifier-selected crop must not crash read_frame() or fabricate a
    value -- the field simply stays None/0 confidence, same as a Tier-1 OCR
    failure already behaves."""
    img = _real_image_array("sample_0017")
    reading, confidences = read_frame(img, engine=_FailingEngine(), roi_extractor=extract_rois_by_field_classifier)

    for field in ("hr", "spo2", "etco2", "temp", "rr"):
        assert reading[field] is None
    assert reading["nibpSystolic"] is None
    assert all(v == 0.0 for v in confidences.values())


def test_confidence_fusion_is_min_not_average():
    """A classifier confidence of 100 fused with an OCR confidence of 10
    must report 10, never (100+10)/2 -- proves the MIN-gate design (sec 07)
    is actually wired in, not just documented."""
    import app.pipeline.tier2_roi as tier2_roi_module

    real_select = tier2_roi_module._select_candidate_for_vital

    def _forced_confidence(candidates):
        box, pred, reason = real_select(candidates)
        if pred is not None:
            pred.confidence = 1.0  # 100% classifier confidence, forced
        return box, pred, reason

    class _LowOcrEngine(OcrEngine):
        def read_vital(self, crop, vital_type):
            if vital_type == "nibp":
                return NibpValue(120.0, 80.0, 92.0), 10.0
            return 42.0, 10.0

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(tier2_roi_module, "_select_candidate_for_vital", _forced_confidence)
        img = _real_image_array("sample_0017")
        reading, confidences = read_frame(img, engine=_LowOcrEngine(), roi_extractor=extract_rois_by_field_classifier)
        found_any = [v for v in VITALS if confidences[v] > 0.0]
        assert found_any, "expected at least one vital found on sample_0017 to exercise fusion"
        for vital in found_any:
            assert confidences[vital] == 10.0, f"{vital}: expected MIN(100, 10)=10, got {confidences[vital]}"
    finally:
        monkeypatch.undo()


# ─── reconcile() receives Tier-2 readings the same as any other source ───


def test_reconcile_receives_and_gates_tier2_readings():
    """Runs a real Tier-2 read_frame() output straight through the real
    reconcile() -- confirms Tier-2 is not a parallel path; it feeds the
    exact same downstream gate every other engine does, including catching
    a physiologically-implausible value even though the classifier itself
    was confident it found HR. Reads RANGE_BOUNDS from rules.py rather than
    hardcoding it (M4.4 widened hr's lower bound to 0 -- see rules.py -- so
    a hardcoded literal here would silently drift out of sync again)."""
    img = _real_image_array("sample_0017")
    raw_reading, confidences = read_frame(img, roi_extractor=extract_rois_by_field_classifier)
    raw_reading["timestamp"] = 1_700_000_000_000

    confirmed = initial_confirmed_state(raw_reading["timestamp"])
    reading, new_confirmed, flagged = reconcile(raw_reading, confidences, confirmed)

    # reconcile()'s "always complete" contract holds for Tier-2 input too.
    assert all(reading.get(f) is not None for f in ("hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr"))
    assert isinstance(flagged, list)
    if raw_reading.get("hr") is not None and not is_in_range("hr", raw_reading["hr"]):
        hr_flag = next((f for f in flagged if f["vital"] == "hr"), None)
        assert hr_flag is not None
        assert "implausible_range" in hr_flag["frameNote"] or "outside the physiologically plausible range" in hr_flag["frameNote"]


# ─── Camera + WS end-to-end with ROI_ENGINE=tier2 ─────────────────────────


def _create_session() -> str:
    body = {"patientId": "PT-TIER2", "procedure": "Tier2 E2E Test", "anesthetist": "Dr. Priya Sharma"}
    r = CLIENT.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_camera_source_reads_real_external_frame_through_tier2(monkeypatch):
    """CameraSource itself is untouched by M3 (per Phase 7) -- this proves
    that leaving ROI_ENGINE=tier2 set in the environment is sufficient for
    the existing camera path to end up using the Tier-2 stage, with zero
    CameraSource code changes, by passing a Tier-2-selecting roi_extractor
    through the same `engine` swap point CameraSource already exposes is
    NOT needed: read_frame()'s own env-based default does the routing."""
    monkeypatch.setenv("ROI_ENGINE", "tier2")
    import app.pipeline.read_frame as read_frame_module

    read_frame_module._default_roi_extractor = None

    channel = "chan-tier2-camera"
    frame_queue.push_frame(channel, _real_image_bytes("sample_0017"))

    async def _first_frame():
        source = CameraSource(channel=channel, interval=0.01)
        gen = source.stream()
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=15.0)
        finally:
            await gen.aclose()

    frame = asyncio.run(_first_frame())
    frame_queue.clear_channel(channel)
    read_frame_module._default_roi_extractor = None

    assert set(frame.per_vital_confidence.keys()) == set(VITALS)
    # At least one vital should have been found by the Tier-2 stage on this
    # clean, held-out real frame -- proves the camera path actually ran
    # candidate generation + FieldCNN, not silently falling back to Tier-1.
    assert any(v > 0.0 for v in frame.per_vital_confidence.values())


def test_ws_source_camera_with_tier2_reaches_frontend_as_reconciled_reading(monkeypatch):
    """Full path: push a real external-monitor frame -> connect
    ?source=camera on the WS with ROI_ENGINE=tier2 set -> confirm a
    'reading' envelope arrives shaped exactly like every other source's,
    already gated through reconcile()."""
    monkeypatch.setenv("ROI_ENGINE", "tier2")
    import app.pipeline.read_frame as read_frame_module

    read_frame_module._default_roi_extractor = None

    session_id = _create_session()
    png_bytes = _real_image_bytes("sample_0017")

    push = CLIENT.post(
        f"/api/pipeline/push-frame/{session_id}",
        files={"file": ("frame.png", png_bytes, "image/png")},
    )
    assert push.status_code == 200

    try:
        with CLIENT.websocket_connect(f"/ws/vitals/{session_id}?source=camera&interval=0.02") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "reading"
            assert set(msg["reading"].keys()) == {
                "hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr", "timestamp",
            }
            assert msg["provenance"] in ("ai_high", "ai_medium", "ai_low")
    finally:
        frame_queue.clear_channel(session_id)
        read_frame_module._default_roi_extractor = None
