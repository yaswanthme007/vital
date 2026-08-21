"""M5.7.1: fixes the product flow (New Case -> mandatory Calibration ->
automatic Active Operation) and the root cause of intermittent calibration
OCR failures.

The routing/state-machine fix is entirely frontend (StartPage/OperationPage/
sessionStore's existing `cameraMode` flag) -- see vital/scripts/
m5_7_1_flow_e2e.mjs for its real-browser proof. This file covers the
backend half: the OCR reliability root cause and its fix.

ROOT CAUSE (see app.pipeline.read_frame.read_frame's `skip_screen_detection`
docstring for the full writeup): read_frame() unconditionally ran every
frame through detect_screen() before handing it to whichever roi_extractor
was active. detect_screen()'s Canny+contour quad search is inherently
nondeterministic frame-to-frame on a real camera (glare/reflection/bezel-
edge visibility) -- when it fires, it perspective-warps the frame to a
DIFFERENT size than the raw frame a calibrated (operator-drawn-box)
extractor's NormalizedBox coordinates were established against. A frame
where it happens to fire silently remaps every calibrated box onto the
wrong pixels; a frame where it doesn't, reads correctly -- exactly the
"same setup, intermittent, some fields fine" symptom this milestone
investigated. Mirrors this repo's established milestone-test pattern (real
modules, real DB via conftest.py, no mocked pipeline internals except where
proving detect_screen() is/isn't called).
"""

import json
import tempfile
import os as _os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.calibration import NormalizedBox
from app.pipeline import read_frame as read_frame_module
from app.pipeline.calibrated_roi import extract_rois_from_boxes
from app.pipeline.detect import detect_screen
from app.pipeline.read_frame import read_frame
from app.sources.camera import CameraSource

client = TestClient(app)


def _box(x, y, w, h):
    return NormalizedBox(x=x, y=y, w=w, h=h)


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


# ─── root cause: detect_screen() vs. calibrated boxes ───────────────────────


def test_skip_screen_detection_bypasses_detect_screen_entirely(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("detect_screen() must not run when skip_screen_detection=True")

    monkeypatch.setattr(read_frame_module, "detect_screen", _boom)
    img = np.zeros((200, 400, 3), dtype=np.uint8)

    def extractor(image):
        return extract_rois_from_boxes(image, {"hr": _box(0.1, 0.1, 0.2, 0.2)})

    # Must not raise.
    read_frame(img, roi_extractor=extractor, skip_screen_detection=True)


def test_default_still_calls_detect_screen_exactly_once(monkeypatch):
    """Byte-identical regression guard: every pre-M5.7.1 call site
    (skip_screen_detection defaults False) must keep calling detect_screen()
    exactly as before."""
    calls = []
    original = read_frame_module.detect_screen

    def _spy(img, *a, **kw):
        calls.append(1)
        return original(img, *a, **kw)

    monkeypatch.setattr(read_frame_module, "detect_screen", _spy)
    img = np.zeros((200, 400, 3), dtype=np.uint8)
    read_frame(img)
    assert len(calls) == 1


def test_calibrated_box_lands_on_the_wrong_pixels_without_the_fix():
    """Deterministic geometric reproduction of the bug: a bright monitor-like
    rectangle inset in a dark background (classic detect_screen fodder --
    the exact screen-vs-bezel contrast a real camera photo has) with a
    known, distinctly-coloured patch standing in for one vital's digits.

    A calibrated box normalized against the RAW frame recovers that patch
    correctly only when detect_screen() is skipped. Without the fix, the
    IDENTICAL normalized box is applied to detect_screen()'s differently-
    sized rectified image instead, and lands on the wrong region entirely --
    this is the root-cause mechanism, reproduced without depending on OCR
    internals (which would make the test flaky) or a physical camera."""
    h, w = 600, 800
    img = np.full((h, w, 3), 20, dtype=np.uint8)  # dark bezel/room
    sx0, sy0, sx1, sy1 = 80, 60, 720, 540  # bright "screen"
    img[sy0:sy1, sx0:sx1] = 200
    # A small, distinctly-coloured patch standing in for one vital's digits,
    # near the screen's top-left -- like a monitor's HR field.
    hx0, hy0, hx1, hy1 = 120, 100, 220, 160
    img[hy0:hy1, hx0:hx1] = (0, 255, 0)

    screen = detect_screen(img)
    assert screen.detected, "fixture must exercise detect_screen() actually finding a quad"
    assert screen.image.shape[:2] != img.shape[:2], (
        "fixture must exercise a SIZE-CHANGING rectification -- otherwise this test "
        "wouldn't be reproducing the bug at all"
    )

    box = {"hr": _box(hx0 / w, hy0 / h, (hx1 - hx0) / w, (hy1 - hy0) / h)}

    def is_the_green_patch(crop) -> bool:
        if crop is None or crop.size == 0:
            return False
        mean = crop.reshape(-1, 3).mean(axis=0)
        return mean[1] > 150 and mean[0] < 100 and mean[2] < 100

    fixed = extract_rois_from_boxes(img, box)["hr"]
    assert is_the_green_patch(fixed.crop), (
        "skip_screen_detection's crop source (the raw frame) must recover the patch "
        "the box was normalized against"
    )

    unfixed = extract_rois_from_boxes(screen.image, box)["hr"]
    assert not is_the_green_patch(unfixed.crop if unfixed else None), (
        "the pre-fix crop source (detect_screen()'s rectified image) must NOT land on "
        "the same region -- this is the bug this test proves"
    )


# ─── call sites correctly thread skip_screen_detection ──────────────────────


def test_verify_endpoint_uses_skip_screen_detection(monkeypatch):
    """/api/calibration/verify's roi_extractor is always operator-drawn
    candidate boxes -- detect_screen() must never run ahead of it."""

    def _boom(*args, **kwargs):
        raise AssertionError("detect_screen() must not run during calibration verify")

    monkeypatch.setattr(read_frame_module, "detect_screen", _boom)

    png_bytes = _rendered_frame_bytes()
    payload = {
        "referenceWidth": 960,
        "referenceHeight": 560,
        "roiBoxes": {"hr": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
    }
    r = client.post(
        "/api/calibration/verify",
        data={"payload": json.dumps(payload)},
        files={"file": ("frame.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text


def test_verify_endpoint_response_includes_diagnostics():
    png_bytes = _rendered_frame_bytes()
    payload = {
        "referenceWidth": 960,
        "referenceHeight": 560,
        "roiBoxes": {
            "hr": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
            # A box entirely outside the frame -- resolves to no crop at
            # all, exercising the diagnostics[vital] = None branch.
            "spo2": {"x": 1.5, "y": 1.5, "w": 0.1, "h": 0.1},
        },
    }
    r = client.post(
        "/api/calibration/verify",
        data={"payload": json.dumps(payload)},
        files={"file": ("frame.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "diagnostics" in body
    assert body["diagnostics"]["spo2"] is None
    assert body["diagnostics"]["hr"] is None or "rawText" in body["diagnostics"]["hr"]


def test_verify_endpoint_never_persists_a_reading_row():
    """Calibration verification is configuration metadata, not patient
    observation history -- an explicit M5.7.1 requirement. Running /verify
    repeatedly against a real session must never create a vital_readings
    row visible through GET /api/sessions/{id}/readings."""
    session = client.post(
        "/api/sessions",
        json={"patientId": "PT-M571-VERIFY", "procedure": "Test", "anesthetist": "Dr. Test"},
    ).json()
    session_id = session["id"]

    png_bytes = _rendered_frame_bytes()
    payload = {
        "referenceWidth": 960,
        "referenceHeight": 560,
        "roiBoxes": {"hr": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
    }
    for _ in range(3):
        r = client.post(
            "/api/calibration/verify",
            data={"payload": json.dumps(payload)},
            files={"file": ("frame.png", png_bytes, "image/png")},
        )
        assert r.status_code == 200

    readings = client.get(f"/api/sessions/{session_id}/readings").json()
    assert readings == []


def test_read_frame_endpoint_threads_skip_screen_detection_with_calibrated_profile():
    """POST /api/pipeline/read-frame must skip detect_screen() when it
    resolved the DB's active (calibrated) profile, and must NOT skip it
    when falling back to the ROI_ENGINE default (no active profile)."""
    from app.db import repo

    db = SessionLocal()
    try:
        repo.invalidate_active_calibration_profile(db)
    finally:
        db.close()

    png_bytes = _rendered_frame_bytes()

    # No active profile -- default path, roiSource == "default".
    r = client.post("/api/pipeline/read-frame", files={"file": ("frame.png", png_bytes, "image/png")})
    assert r.status_code == 200
    assert r.json()["roiSource"] == "default"

    # Save a valid profile, then confirm the endpoint reports roiSource ==
    # "calibrated" -- read_frame_endpoint's own skip_screen_detection=
    # (roi_source == "calibrated") wiring is what this proves indirectly;
    # a direct detect_screen()-not-called assertion would require reaching
    # into a closure FastAPI owns, so this checks the observable contract
    # (M5.6's own promotion test uses the same roiSource-based approach).
    save_body = {
        "referenceWidth": 960,
        "referenceHeight": 560,
        "roiBoxes": {"hr": {"x": 0.1, "y": 0.05, "w": 0.2, "h": 0.15}},
        "fieldMeta": {"hr": {"verified": True, "verifiedValue": "74", "verifiedConfidence": 95.0}},
    }
    saved = client.post("/api/calibration", json=save_body)
    assert saved.status_code == 201, saved.text

    r2 = client.post("/api/pipeline/read-frame", files={"file": ("frame.png", png_bytes, "image/png")})
    assert r2.status_code == 200
    assert r2.json()["roiSource"] == "calibrated"

    db = SessionLocal()
    try:
        repo.invalidate_active_calibration_profile(db)
    finally:
        db.close()


def test_camera_source_accepts_and_threads_skip_screen_detection(monkeypatch):
    """CameraSource's new skip_screen_detection param must flow through to
    read_frame() exactly like roi_extractor already does."""
    import asyncio

    from app.sources import frame_queue

    seen = {}

    def _fake_read_frame(img, engine=None, roi_extractor=None, crop_integrity=None,
                          diagnostics=None, skip_screen_detection=False):
        seen["skip_screen_detection"] = skip_screen_detection
        return {"hr": None, "spo2": None, "nibpSystolic": None, "nibpDiastolic": None,
                "nibpMean": None, "etco2": None, "temp": None, "rr": None}, {}

    monkeypatch.setattr("app.sources.camera.read_frame", _fake_read_frame)

    png_bytes = _rendered_frame_bytes()
    channel = "chan-skip-screen-detection"
    frame_queue.push_frame(channel, png_bytes)

    async def _first_frame():
        source = CameraSource(channel=channel, interval=0.01, skip_screen_detection=True)
        gen = source.stream()
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=6.0)
        finally:
            await gen.aclose()

    asyncio.run(_first_frame())
    assert seen["skip_screen_detection"] is True


def test_camera_source_defaults_skip_screen_detection_to_false(monkeypatch):
    import asyncio

    from app.sources import frame_queue

    seen = {}

    def _fake_read_frame(img, engine=None, roi_extractor=None, crop_integrity=None,
                          diagnostics=None, skip_screen_detection=False):
        seen["skip_screen_detection"] = skip_screen_detection
        return {"hr": None, "spo2": None, "nibpSystolic": None, "nibpDiastolic": None,
                "nibpMean": None, "etco2": None, "temp": None, "rr": None}, {}

    monkeypatch.setattr("app.sources.camera.read_frame", _fake_read_frame)

    png_bytes = _rendered_frame_bytes()
    channel = "chan-skip-screen-detection-default"
    frame_queue.push_frame(channel, png_bytes)

    async def _first_frame():
        source = CameraSource(channel=channel, interval=0.01)
        gen = source.stream()
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=6.0)
        finally:
            await gen.aclose()

    asyncio.run(_first_frame())
    assert seen["skip_screen_detection"] is False
