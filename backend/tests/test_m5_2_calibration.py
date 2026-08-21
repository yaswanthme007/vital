"""M5.2: calibration schema, geometry validation, coordinate mapping,
persistence, API lifecycle, and camera-source wiring. Mirrors this repo's
established milestone-test pattern (test_m5_1_ocr_confidence_restoration.py,
test_roi_engine_selection.py) -- real modules, real DB (via conftest.py's
temp-file SQLite), no mocked pipeline internals.
"""

import numpy as np
from fastapi.testclient import TestClient

from app.db import repo
from app.db.session import SessionLocal
from app.main import app
from app.models.calibration import CalibrationFieldMeta, CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import aspect_ratio_drift, extract_rois_from_boxes, make_extractor
from app.pipeline.calibration_validate import validate_profile

client = TestClient(app)


def _box(x, y, w, h):
    return NormalizedBox(x=x, y=y, w=w, h=h)


def _verified_meta():
    return CalibrationFieldMeta(verified=True, verified_value="74", verified_confidence=95.0)


def _valid_profile(**overrides) -> CalibrationProfile:
    defaults = dict(
        id="pending",
        reference_width=1280,
        reference_height=720,
        roi_boxes={"hr": _box(0.5, 0.05, 0.15, 0.1), "spo2": _box(0.5, 0.2, 0.15, 0.1)},
        field_meta={"hr": _verified_meta(), "spo2": _verified_meta()},
        created_at=0,
        updated_at=0,
    )
    defaults.update(overrides)
    return CalibrationProfile(**defaults)


# ─── coordinate mapping (calibrated_roi.py) ─────────────────────────────────


def test_normalized_box_maps_to_expected_pixel_region():
    img = np.zeros((200, 400, 3), dtype=np.uint8)
    img[20:60, 40:120] = 255  # a bright block inside where the box should land
    boxes = {"hr": _box(0.1, 0.1, 0.2, 0.2)}  # x*400=40, y*200=20, w*400=80, h*200=40
    result = extract_rois_from_boxes(img, boxes)
    assert result["hr"] is not None
    x, y, w, h = result["hr"].box
    assert (x, y, w, h) == (40, 20, 80, 40)
    assert np.all(result["hr"].crop == 255)


def test_same_normalized_box_maps_deterministically_across_calls():
    """Phase 5: 'the same physical screen location should map to the same
    vital ROI' -- calling the extractor twice on the same frame must yield
    byte-identical boxes."""
    img = np.random.randint(0, 255, (300, 500, 3), dtype=np.uint8)
    boxes = {"hr": _box(0.2, 0.3, 0.1, 0.1)}
    r1 = extract_rois_from_boxes(img, boxes)
    r2 = extract_rois_from_boxes(img, boxes)
    assert r1["hr"].box == r2["hr"].box
    assert np.array_equal(r1["hr"].crop, r2["hr"].crop)


def test_box_resolves_identically_across_different_resolutions_same_aspect():
    """Phase 6: resolution changes (same aspect ratio) must not change WHERE
    the box lands relative to the frame content."""
    boxes = {"hr": _box(0.25, 0.25, 0.5, 0.5)}
    small = np.zeros((200, 200, 3), dtype=np.uint8)
    small[50:150, 50:150] = 255
    big = np.zeros((800, 800, 3), dtype=np.uint8)
    big[200:600, 200:600] = 255

    r_small = extract_rois_from_boxes(small, boxes)["hr"]
    r_big = extract_rois_from_boxes(big, boxes)["hr"]
    assert np.all(r_small.crop == 255)
    assert np.all(r_big.crop == 255)
    # Same fraction of the frame in both cases.
    assert r_small.box == (50, 50, 100, 100)
    assert r_big.box == (200, 200, 400, 400)


def test_box_outside_frame_bounds_is_clipped_not_crashed():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    boxes = {"hr": _box(0.9, 0.9, 0.5, 0.5)}  # extends past the right/bottom edge
    result = extract_rois_from_boxes(img, boxes)
    assert result["hr"] is not None
    x, y, w, h = result["hr"].box
    assert x + w <= 100 and y + h <= 100


def test_vital_missing_from_roi_boxes_resolves_to_none():
    """read_frame() relies on dict.get(vital) returning None for a vital
    the operator never drew -- e.g. a monitor that never shows NIBP
    (EVIDENCE.md sec 9)."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    result = extract_rois_from_boxes(img, {"hr": _box(0.1, 0.1, 0.1, 0.1)})
    assert "nibp" not in result


def test_aspect_ratio_drift_zero_for_identical_ratio():
    assert aspect_ratio_drift(1280, 720, 1920, 1080) == 0.0


def test_aspect_ratio_drift_nonzero_for_different_ratio():
    drift = aspect_ratio_drift(1280, 720, 1000, 1000)  # 16:9 vs 1:1
    assert drift > 0.3


def test_extractor_withholds_all_fields_on_large_aspect_drift():
    """Phase 12 fail-safe: a frame that looks like a different camera/
    framing must withhold every field rather than mapping boxes onto
    content calibration says nothing about."""
    profile = _valid_profile(reference_width=1280, reference_height=720)
    extractor = make_extractor(profile)
    # A near-square frame (1:1) vs. the profile's 16:9 reference.
    weird_frame = np.zeros((1000, 1000, 3), dtype=np.uint8)
    result = extractor(weird_frame)
    assert all(v is None for v in result.values())


def test_extractor_extracts_normally_within_aspect_tolerance():
    profile = _valid_profile(reference_width=1280, reference_height=720)
    extractor = make_extractor(profile)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)  # same 16:9 aspect, different resolution
    result = extractor(frame)
    assert result["hr"] is not None
    assert result["spo2"] is not None


# ─── geometry validation (calibration_validate.py) ──────────────────────────


def test_valid_profile_has_no_errors():
    assert validate_profile(_valid_profile()) == []


def test_empty_roi_boxes_rejected():
    profile = _valid_profile(roi_boxes={}, field_meta={})
    errors = validate_profile(profile)
    assert any("at least one" in e.lower() for e in errors)


def test_box_outside_frame_rejected():
    profile = _valid_profile(roi_boxes={"hr": _box(0.9, 0.9, 0.5, 0.5)}, field_meta={"hr": _verified_meta()})
    errors = validate_profile(profile)
    assert any("outside the frame" in e for e in errors)


def test_too_small_box_rejected():
    profile = _valid_profile(roi_boxes={"hr": _box(0.5, 0.5, 0.001, 0.001)}, field_meta={"hr": _verified_meta()})
    errors = validate_profile(profile)
    assert any("too small" in e for e in errors)


def test_too_large_box_rejected():
    """EVIDENCE.md sec 6.1's 'too generous' case: a box pulling in half the
    screen isn't a single field."""
    profile = _valid_profile(roi_boxes={"hr": _box(0.1, 0.1, 0.7, 0.7)}, field_meta={"hr": _verified_meta()})
    errors = validate_profile(profile)
    assert any("too large" in e for e in errors)


def test_extreme_aspect_ratio_box_rejected():
    """Looks like a waveform trace, not a numeral -- EVIDENCE.md's
    candidate-generation section describes exactly this shape."""
    profile = _valid_profile(roi_boxes={"hr": _box(0.1, 0.1, 0.6, 0.02)}, field_meta={"hr": _verified_meta()})
    errors = validate_profile(profile)
    assert any("trace" in e for e in errors)


def test_overlapping_boxes_rejected():
    profile = _valid_profile(
        roi_boxes={"hr": _box(0.1, 0.1, 0.2, 0.2), "spo2": _box(0.15, 0.15, 0.2, 0.2)},
        field_meta={"hr": _verified_meta(), "spo2": _verified_meta()},
    )
    errors = validate_profile(profile)
    assert any("overlap" in e for e in errors)


def test_unverified_field_blocks_save():
    """The hard gate ROADMAP.md/ARCHITECTURE.md both call out: Verify must
    block Save on an unconfirmed field."""
    profile = _valid_profile(field_meta={"hr": _verified_meta()})  # spo2 present but unverified
    errors = validate_profile(profile)
    assert any("unverified" in e.lower() and "spo2" in e for e in errors)


def test_zero_or_negative_size_box_rejected():
    profile = _valid_profile(roi_boxes={"hr": _box(0.1, 0.1, 0.0, 0.1)}, field_meta={"hr": _verified_meta()})
    errors = validate_profile(profile)
    assert any("zero or negative" in e for e in errors)


# ─── persistence (db/repo.py) ────────────────────────────────────────────


def test_save_and_get_active_calibration_profile_roundtrips():
    db = SessionLocal()
    try:
        assert repo.get_active_calibration_profile(db) is None
        saved = repo.save_calibration_profile(
            db,
            {
                "reference_width": 1280,
                "reference_height": 720,
                "roi_boxes": {"hr": {"x": 0.5, "y": 0.05, "w": 0.15, "h": 0.1}},
                "field_meta": {"hr": {"verified": True, "verifiedValue": "74", "verifiedConfidence": 95.0}},
            },
        )
        assert saved.is_active is True
        assert saved.id.startswith("CAL-")

        fetched = repo.get_active_calibration_profile(db)
        assert fetched is not None
        assert fetched.id == saved.id
        assert fetched.roi_boxes["hr"].x == 0.5
    finally:
        db.close()


def test_saving_a_new_profile_deactivates_the_previous_one():
    db = SessionLocal()
    try:
        first = repo.save_calibration_profile(
            db, {"reference_width": 100, "reference_height": 100, "roi_boxes": {"hr": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}}}
        )
        second = repo.save_calibration_profile(
            db, {"reference_width": 200, "reference_height": 200, "roi_boxes": {"hr": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}}}
        )
        active = repo.get_active_calibration_profile(db)
        assert active.id == second.id
        assert repo.get_calibration_profile(db, first.id).is_active is False
    finally:
        db.close()


def test_invalidate_active_calibration_profile():
    db = SessionLocal()
    try:
        repo.save_calibration_profile(
            db, {"reference_width": 100, "reference_height": 100, "roi_boxes": {"hr": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}}}
        )
        assert repo.get_active_calibration_profile(db) is not None
        assert repo.invalidate_active_calibration_profile(db) is True
        assert repo.get_active_calibration_profile(db) is None
        # Invalidating again with nothing active is a safe no-op, not an error.
        assert repo.invalidate_active_calibration_profile(db) is False
    finally:
        db.close()


# ─── API lifecycle ────────────────────────────────────────────────────────


def _png_bytes(color=(0, 0, 0), size=(200, 200)) -> bytes:
    import io as _io

    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_get_active_returns_404_when_none_saved():
    r = client.get("/api/calibration/active")
    assert r.status_code == 404


def test_verify_endpoint_runs_real_ocr_on_candidate_boxes():
    from simulator.render.monitor_layout import render_monitor

    import tempfile
    import os as _os

    reading = {
        "hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78, "nibpMean": 92,
        "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1700000000000,
    }
    fd, path = tempfile.mkstemp(suffix=".png")
    _os.close(fd)
    try:
        render_monitor(reading, path, layout="grid")
        with open(path, "rb") as f:
            png_bytes = f.read()
    finally:
        _os.remove(path)

    from PIL import Image
    with Image.open(__import__("io").BytesIO(png_bytes)) as im:
        w, h = im.size

    # HR's real on-screen box for the "grid" layout, per test_pipeline_roi's
    # own colour-ROI expectations -- approximate, normalized generously so a
    # render-layout tweak doesn't make this test flaky; the assertion below
    # only requires reading BACK the right value, not a tight box.
    payload = {
        "referenceWidth": w,
        "referenceHeight": h,
        "roiBoxes": {"hr": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
    }
    import json

    r = client.post(
        "/api/calibration/verify",
        data={"payload": json.dumps(payload)},
        files={"file": ("frame.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "reading" in body and "confidence" in body
    assert body["frameWidth"] == w and body["frameHeight"] == h


def test_save_profile_rejects_unverified_field_with_422():
    body = {
        "referenceWidth": 1280,
        "referenceHeight": 720,
        "roiBoxes": {"hr": {"x": 0.5, "y": 0.05, "w": 0.15, "h": 0.1}},
        "fieldMeta": {},
    }
    r = client.post("/api/calibration", json=body)
    assert r.status_code == 422
    assert "errors" in r.json()["detail"]


def test_save_profile_rejects_invalid_geometry_with_422():
    body = {
        "referenceWidth": 1280,
        "referenceHeight": 720,
        "roiBoxes": {"hr": {"x": 0.5, "y": 0.05, "w": 5.0, "h": 5.0}},
        "fieldMeta": {"hr": {"verified": True}},
    }
    r = client.post("/api/calibration", json=body)
    assert r.status_code == 422


def test_save_then_get_active_then_invalidate_full_lifecycle():
    body = {
        "referenceWidth": 1280,
        "referenceHeight": 720,
        "roiBoxes": {"hr": {"x": 0.5, "y": 0.05, "w": 0.15, "h": 0.1}},
        "fieldMeta": {"hr": {"verified": True, "verifiedValue": "74", "verifiedConfidence": 95.0}},
    }
    r = client.post("/api/calibration", json=body)
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["isActive"] is True

    r = client.get("/api/calibration/active")
    assert r.status_code == 200
    assert r.json()["id"] == saved["id"]

    r = client.delete("/api/calibration/active")
    assert r.status_code == 204

    r = client.get("/api/calibration/active")
    assert r.status_code == 404


# ─── camera source / WS wiring ──────────────────────────────────────────────


def test_camera_source_accepts_and_uses_custom_roi_extractor():
    """CameraSource's new roi_extractor param (M5.2) must actually flow
    through to read_frame() -- proven by a fake extractor that returns a
    known-good HR crop regardless of the pushed frame's real content."""
    import asyncio

    from app.pipeline.types import VitalRoiResult
    from app.sources import frame_queue
    from app.sources.camera import CameraSource
    from simulator.render.monitor_layout import render_monitor
    import tempfile, os as _os

    reading = {
        "hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78, "nibpMean": 92,
        "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1700000000000,
    }
    fd, path = tempfile.mkstemp(suffix=".png")
    _os.close(fd)
    try:
        render_monitor(reading, path, layout="grid")
        with open(path, "rb") as f:
            png_bytes = f.read()
    finally:
        _os.remove(path)

    def fake_extractor(img):
        # Return nothing for every vital -- proves the CUSTOM extractor ran
        # (a real default extractor would have found real content).
        return {"hr": None, "spo2": None, "nibp": None, "etco2": None, "temp": None, "rr": None}

    channel = "chan-custom-extractor"
    frame_queue.push_frame(channel, png_bytes)

    async def _first_frame():
        source = CameraSource(channel=channel, interval=0.01, roi_extractor=fake_extractor)
        gen = source.stream()
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=6.0)
        finally:
            await gen.aclose()

    frame = asyncio.run(_first_frame())
    frame_queue.clear_channel(channel)
    assert frame.reading["hr"] is None  # NOT 74 -- proves the custom (empty) extractor was used, not the default


def test_ws_camera_path_uses_active_calibration_profile_end_to_end():
    """The full product path Phase 10/11 ask for, run against a real
    (synthetic) frame: save a real, GEOMETRICALLY ACCURATE calibration
    profile (boxes derived from the simulator's own ground-truth ROIs, not
    hand-guessed), then connect ?source=camera on the WS and confirm the
    reconciled reading actually reflects the calibrated crop -- proving
    app.ws.vitals._camera_roi_extractor really looked up and used the
    active profile, not the ROI_ENGINE default."""
    import json
    import os as _os
    import tempfile

    from simulator.render.monitor_layout import render_monitor

    reading = {
        "hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 78, "nibpMean": 92,
        "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": 1700000000000,
    }
    fd, path = tempfile.mkstemp(suffix=".png")
    _os.close(fd)
    try:
        label = render_monitor(reading, path, layout="grid")
        with open(path, "rb") as f:
            png_bytes = f.read()
    finally:
        _os.remove(path)

    from PIL import Image
    with Image.open(__import__("io").BytesIO(png_bytes)) as im:
        frame_w, frame_h = im.size

    # Ground-truth pixel ROI -> normalized box, exactly as an operator
    # drawing tightly around the current digits would produce -- WIDTH
    # padding is intentionally NOT added here: app.api.calibration.
    # save_profile already applies its own automatic width safety margin
    # (calibrated_roi.WIDTH_SAFETY_PAD_FRACTION) at save time, and stacking
    # a second, test-only pad on top of it was observed to over-widen the
    # box enough to bleed into this tightly-packed layout's neighbouring
    # glyph and misread "74" as "75" -- a real instance of EVIDENCE.md sec
    # 6.1's "over-generous boxes hurt too" finding, not a test bug to paper
    # over by widening further. A small height-only pad avoids clipping the
    # glyph's top/stroke (height isn't auto-padded server-side, since digit
    # count -- the actual failure mode -- never changes a field's height).
    gx, gy, gw, gh = label["rois"]["hr"]
    pad_h = gh * 0.1
    roi_boxes = {
        "hr": {
            "x": gx / frame_w,
            "y": max(0.0, (gy - pad_h)) / frame_h,
            "w": gw / frame_w,
            "h": (gh + 2 * pad_h) / frame_h,
        }
    }

    save_body = {
        "referenceWidth": frame_w,
        "referenceHeight": frame_h,
        "roiBoxes": roi_boxes,
        "fieldMeta": {"hr": {"verified": True, "verifiedValue": "74", "verifiedConfidence": 95.0}},
    }
    r = client.post("/api/calibration", json=save_body)
    assert r.status_code == 201, r.text

    session_r = client.post(
        "/api/sessions", json={"patientId": "PT-1", "procedure": "Test", "anesthetist": "Dr. Priya Sharma"}
    )
    session_id = session_r.json()["id"]

    from app.sources import frame_queue

    push = client.post(
        f"/api/pipeline/push-frame/{session_id}", files={"file": ("frame.png", png_bytes, "image/png")}
    )
    assert push.status_code == 200

    try:
        with client.websocket_connect(f"/ws/vitals/{session_id}?source=camera&interval=0.02") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "reading"
            # M5.8: one frame no longer confirms anything on the camera path
            # (app.validation.live_corroboration) -- and, critically, an
            # unconfirmed field is null rather than a fabricated baseline.
            assert msg["reading"]["hr"] is None
            assert msg["fieldStatus"]["hr"] == "unknown"

            # Feed further frames of the same monitor until the calibrated
            # box's value is corroborated. Each push must be a NEW frame:
            # CameraSource only processes a sequence number it has not seen
            # (app.sources.frame_queue's "latest frame wins" contract), which
            # is exactly what a browser pushing ~1 frame/second produces.
            confirmed = None
            for _ in range(8):
                client.post(
                    f"/api/pipeline/push-frame/{session_id}",
                    files={"file": ("frame.png", png_bytes, "image/png")},
                )
                msg = ws.receive_json()
                while msg["type"] != "reading":
                    msg = ws.receive_json()
                if msg["fieldStatus"].get("hr") == "confirmed":
                    confirmed = msg
                    break
            assert confirmed is not None, "expected the calibrated HR box to confirm across frames"
            assert confirmed["reading"]["hr"] == 74
    finally:
        frame_queue.clear_channel(session_id)
        client.delete("/api/calibration/active")
