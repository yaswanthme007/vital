import asyncio
import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.sources import frame_queue
from app.validation.reconcile import DEFAULT_BASELINE
from app.sources.camera import CameraSource
from simulator.render.monitor_layout import render_monitor

client = TestClient(app)

VITAL_READING_KEYS = {
    "hr",
    "spo2",
    "nibpSystolic",
    "nibpDiastolic",
    "nibpMean",
    "etco2",
    "temp",
    "rr",
    "timestamp",
}


def _sample_reading():
    return {
        "hr": 72,
        "spo2": 98,
        "nibpSystolic": 120,
        "nibpDiastolic": 78,
        "nibpMean": 92,
        "etco2": 38,
        "temp": 36.8,
        "rr": 14,
        "timestamp": 1700000000000,
    }


def _rendered_png_bytes(tmp_path, layout="grid") -> bytes:
    out_path = tmp_path / f"{layout}.png"
    render_monitor(_sample_reading(), str(out_path), layout=layout)
    return out_path.read_bytes()


def _create_session(**overrides) -> str:
    body = {"patientId": "PT-1", "procedure": "Test", "anesthetist": "Dr. Priya Sharma"}
    body.update(overrides)
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ─── frame_queue: push/get/clear ─────────────────────────────────────────────


def test_frame_queue_push_then_get_roundtrips():
    frame_queue.push_frame("chan-1", b"fake-bytes")
    item = frame_queue.get_latest_frame("chan-1")
    assert item is not None
    assert item.data == b"fake-bytes"
    frame_queue.clear_channel("chan-1")


def test_frame_queue_push_overwrites_not_buffers():
    """'Latest frame wins' -- a channel holds at most one pending frame."""
    seq1 = frame_queue.push_frame("chan-2", b"frame-a")
    seq2 = frame_queue.push_frame("chan-2", b"frame-b")
    assert seq2 == seq1 + 1

    item = frame_queue.get_latest_frame("chan-2")
    assert item.data == b"frame-b"
    assert item.seq == seq2
    frame_queue.clear_channel("chan-2")


def test_frame_queue_get_unknown_channel_returns_none():
    assert frame_queue.get_latest_frame("never-pushed") is None


def test_frame_queue_clear_removes_pending_frame():
    frame_queue.push_frame("chan-3", b"x")
    frame_queue.clear_channel("chan-3")
    assert frame_queue.get_latest_frame("chan-3") is None


# ─── CameraSource.stream(): real pipeline, no camera hardware needed ────────


def test_camera_source_yields_nothing_until_a_frame_is_pushed():
    """No frame pushed yet -> stream() must not yield (and must not crash
    trying to decode nothing)."""

    async def _drain_with_timeout():
        source = CameraSource(channel="chan-empty", interval=0.01)
        gen = source.stream()
        try:
            await asyncio.wait_for(gen.__anext__(), timeout=0.1)
            return "yielded"
        except asyncio.TimeoutError:
            return "timed-out"
        finally:
            await gen.aclose()

    result = asyncio.run(_drain_with_timeout())
    assert result == "timed-out"


def test_camera_source_reads_pushed_frame_through_real_pipeline(tmp_path):
    """Pushes one real rendered monitor frame and confirms CameraSource runs
    it through the actual detect -> colour-ROI -> OCR pipeline (not a stub)
    and yields a Frame shaped per sources/base.py's contract."""
    channel = "chan-pipeline"
    png_bytes = _rendered_png_bytes(tmp_path)
    frame_queue.push_frame(channel, png_bytes)

    async def _first_frame():
        source = CameraSource(channel=channel, interval=0.01)
        gen = source.stream()
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=6.0)
        finally:
            await gen.aclose()

    frame = asyncio.run(_first_frame())
    frame_queue.clear_channel(channel)

    assert set(frame.reading.keys()) == VITAL_READING_KEYS
    # Clean, unaugmented render at VITAL's own colour palette -- HR should
    # read back exactly, same standard the roi.py test suite already holds
    # extract_rois_by_colour to.
    assert frame.reading["hr"] == 72
    assert frame.per_vital_confidence["hr"] > 80
    assert frame.provenance in ("ai_high", "ai_medium", "ai_low")
    assert frame.source_frame_id == f"{channel}-1"


def test_camera_source_skips_corrupt_frame_without_crashing():
    channel = "chan-corrupt"
    frame_queue.push_frame(channel, b"not a real image")

    async def _drain_with_timeout():
        source = CameraSource(channel=channel, interval=0.01)
        gen = source.stream()
        try:
            await asyncio.wait_for(gen.__anext__(), timeout=0.1)
            return "yielded"
        except asyncio.TimeoutError:
            return "timed-out"
        finally:
            await gen.aclose()

    result = asyncio.run(_drain_with_timeout())
    frame_queue.clear_channel(channel)
    assert result == "timed-out"  # corrupt frame silently skipped, stream still alive


def test_camera_source_only_reprocesses_on_a_new_seq(tmp_path):
    """Two ticks with no new push in between must yield only once -- the
    same pushed frame is never processed twice."""
    channel = "chan-dedupe"
    png_bytes = _rendered_png_bytes(tmp_path)
    frame_queue.push_frame(channel, png_bytes)

    async def _collect_two_ticks():
        source = CameraSource(channel=channel, interval=0.02)
        gen = source.stream()
        results = []
        try:
            results.append(await asyncio.wait_for(gen.__anext__(), timeout=6.0))
            # No new push here -- the second tick should time out, not yield.
            try:
                results.append(await asyncio.wait_for(gen.__anext__(), timeout=0.15))
            except asyncio.TimeoutError:
                pass
        finally:
            await gen.aclose()
        return results

    results = asyncio.run(_collect_two_ticks())
    frame_queue.clear_channel(channel)
    assert len(results) == 1


# ─── push-frame endpoint: ingestion only, no OCR/reconcile inline ──────────


def test_push_frame_endpoint_stores_frame_without_running_ocr(tmp_path):
    png_bytes = _rendered_png_bytes(tmp_path)
    r = client.post(
        "/api/pipeline/push-frame/test-channel",
        files={"file": ("frame.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    # Only an ack of the push -- no "reading"/"confidence" keys, i.e. this
    # endpoint never ran read_frame()/reconcile() itself.
    assert set(body.keys()) == {"channel", "seq"}
    assert body["channel"] == "test-channel"
    assert isinstance(body["seq"], int)

    item = frame_queue.get_latest_frame("test-channel")
    assert item is not None
    assert item.data == png_bytes
    frame_queue.clear_channel("test-channel")


def test_push_frame_endpoint_rejects_non_image_with_422():
    r = client.post(
        "/api/pipeline/push-frame/test-channel-2",
        files={"file": ("frame.txt", b"not an image", "text/plain")},
    )
    assert r.status_code == 422
    frame_queue.clear_channel("test-channel-2")


# ─── WS end-to-end: source=camera flows through send_loop -> reconcile ─────


def _push(session_id: str, png_bytes: bytes):
    return client.post(
        f"/api/pipeline/push-frame/{session_id}",
        files={"file": ("frame.png", png_bytes, "image/png")},
    )


def _read_until_hr_confirmed(ws, session_id: str, png_bytes: bytes, max_frames: int = 10):
    """Push fresh frames until HR is genuinely confirmed, or give up.

    M5.8: the camera path no longer confirms anything off ONE frame. A value
    must be reproduced by several recent frames, all clean of residual OCR
    content, before it becomes a confirmed observation -- see
    app.validation.live_corroboration for the measured reason (the demo
    recording's SpO2 92/94/96/97/99, EtCO2 4 and RR 42 ledger rows were each
    a single frame that happened to clear the old 70%-confidence gate).

    Each iteration pushes a NEW frame because CameraSource only processes a
    frame whose sequence number it has not seen (frame_queue's "latest frame
    wins" contract) -- re-reading the same pushed bytes would produce no
    further ticks at all. A real browser pushes ~1 frame/second, so several
    independent frames is exactly what the live path gets."""
    for _ in range(max_frames):
        _push(session_id, png_bytes)
        msg = ws.receive_json()
        while msg["type"] != "reading":
            msg = ws.receive_json()
        if msg["fieldStatus"].get("hr") == "confirmed":
            return msg
    return None


def test_ws_source_camera_streams_reconciled_reading_from_pushed_frame(tmp_path):
    """The full path: push a real frame over REST, connect
    ?source=camera on the WS, and confirm a 'reading' envelope comes back
    shaped exactly like every other source's -- i.e. it went through
    send_loop -> reconcile(), not a shortcut."""
    session_id = _create_session()
    png_bytes = _rendered_png_bytes(tmp_path)

    push = client.post(
        f"/api/pipeline/push-frame/{session_id}",
        files={"file": ("frame.png", png_bytes, "image/png")},
    )
    assert push.status_code == 200

    with client.websocket_connect(f"/ws/vitals/{session_id}?source=camera&interval=0.02") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "reading"
        assert set(msg["reading"].keys()) == VITAL_READING_KEYS
        assert set(msg["confidence"].keys()) == {"hr", "spo2", "nibp", "etco2", "temp", "rr"}

        # M5.8: the FIRST frame confirms nothing -- and, critically, it does
        # not substitute a plausible placeholder either. Everything is null
        # and 'unknown' until the camera has actually corroborated a value.
        assert msg["reading"]["hr"] is None
        assert msg["fieldStatus"]["hr"] == "unknown"

        # Keep the same frame flowing; once enough frames agree, the real
        # camera-read value (72 -- the value the fixture renders) is
        # confirmed. This is what proves reconcile() ran on real OCR output
        # rather than the raw read_frame() passthrough.
        confirmed = _read_until_hr_confirmed(ws, session_id, png_bytes)
        assert confirmed is not None, "expected HR to confirm once several frames agreed"
        assert confirmed["reading"]["hr"] == 72

    frame_queue.clear_channel(session_id)


def test_ws_source_camera_never_reports_a_value_it_has_not_observed(tmp_path):
    """M5.8 regression guard for the defect this milestone exists to fix: a
    camera session used to open with every field seeded from
    reconcile.DEFAULT_BASELINE (HR 75, SpO2 98, Temp 36.8, RR 14), which the
    next tick then re-labelled 'held' -- so a fabricated number was shown as
    "Held - last confirmed hh:mm:ss" for the whole case, indistinguishable
    from something the camera had genuinely seen. Nothing in the first
    envelope may be a DEFAULT_BASELINE value."""
    session_id = _create_session()

    with client.websocket_connect(f"/ws/vitals/{session_id}?source=camera&interval=0.02") as ws:
        png_bytes = _rendered_png_bytes(tmp_path)
        client.post(
            f"/api/pipeline/push-frame/{session_id}",
            files={"file": ("frame.png", png_bytes, "image/png")},
        )
        msg = ws.receive_json()
        assert msg["type"] == "reading"
        for field, baseline in DEFAULT_BASELINE.items():
            assert msg["reading"][field] is None, f"{field} was seeded with a value the camera never read"
            assert msg["fieldStatus"][field] == "unknown"
            assert msg["reading"][field] != baseline

    frame_queue.clear_channel(session_id)


def test_ws_source_camera_with_no_pushed_frame_just_waits(tmp_path):
    """No frame ever pushed for this session -- the WS connection must stay
    open (not error/close) rather than crash waiting for one, since a real
    camera feed may start a beat after the WS connects."""
    session_id = _create_session()

    with client.websocket_connect(f"/ws/vitals/{session_id}?source=camera&interval=0.02") as ws:
        # Push arrives "late", after the connection is already up.
        png_bytes = _rendered_png_bytes(tmp_path)
        client.post(
            f"/api/pipeline/push-frame/{session_id}",
            files={"file": ("frame.png", png_bytes, "image/png")},
        )
        msg = ws.receive_json()
        assert msg["type"] == "reading"
        confirmed = _read_until_hr_confirmed(ws, session_id, png_bytes)
        assert confirmed is not None
        assert confirmed["reading"]["hr"] == 72

    frame_queue.clear_channel(session_id)
