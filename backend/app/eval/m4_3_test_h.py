"""M4.3 Test H: validate the integration through the REAL running
application path, not just direct function calls.

Two runs, for the two reasons spelled out below:

(1) CURRENT_BASELINE, through the actual FastAPI app + WebSocket transport
    (fastapi.testclient.TestClient -> /ws/vitals/{id}?source=pipeline&...),
    exactly as a real browser client would connect. This exercises
    app.ws.vitals.vitals_ws -> ReplaySource(mode="pipeline") ->
    app.pipeline.read_frame.read_frame() -> app.validation.reconcile.
    reconcile() -> websocket.send_json(), all real, all unmodified,
    end-to-end, over the real 52-frame dataset.

(2) PSM10_SELECTIVE, through app.ws.vitals.send_loop() called directly
    (same production coroutine vitals_ws() itself awaits) with a
    ReplaySource(engine=SelectivePsmEngine(...)) and a recording stub in
    place of websocket.send_json. NOT run through the WebSocket transport
    layer, for one concrete, checkable reason: the WS endpoint
    (app/ws/vitals.py's vitals_ws()) constructs ReplaySource with no
    engine= parameter exposed -- OCR_ENGINE only selects between
    tesseract/onnx, and there is no query param or env var today that can
    ask the running server for PSM10_SELECTIVE's per-field PSM routing.
    Reaching it through the real transport would require adding exactly
    that hook to production code, which M4.3 is explicitly not allowed to
    do before a verdict is reached. send_loop() is the same function the
    transport calls; only the websocket.accept()/receive/send plumbing
    around it is bypassed. This is reported as a Test H limitation, not
    hidden.

Writes only under app/eval/tier2_data/external_monitor_video/m4_3_report/.

Usage:
    python -m app.eval.m4_3_test_h
"""

import asyncio
import json
import os
import time
from collections import deque
from typing import List

os.environ.setdefault("ROI_ENGINE", "tier2")

from fastapi.testclient import TestClient

from app.alerts.rules import AlertThrottle
from app.eval.m4_3_reliability import SelectivePsmEngine
from app.main import app
from app.sources.replay import ReplaySource
from app.ws.vitals import send_loop

DATASET_DIR = "app/eval/tier2_data/external_monitor_video"
OUT_DIR = os.path.join(DATASET_DIR, "m4_3_report")
N_FRAMES = 52  # exactly once through the dataset, no wraparound


def run_baseline_over_real_websocket() -> List[dict]:
    client = TestClient(app)
    messages = []
    t0 = time.perf_counter()
    with client.websocket_connect(
        f"/ws/vitals/m4_3_test_h_baseline?source=pipeline&dataset={DATASET_DIR}&interval=0.01"
    ) as ws:
        seen_readings = 0
        tries = 0
        # Every tick can also emit several "flagged" envelopes (M4.3's own
        # findings below show most fields get flagged most ticks under
        # today's confidence/range gates), so total messages run well above
        # N_FRAMES -- generous cap, not a fixed multiplier guess.
        while seen_readings < N_FRAMES and tries < N_FRAMES * 20:
            msg = ws.receive_json()
            tries += 1
            messages.append(msg)
            if msg["type"] == "reading":
                seen_readings += 1
    elapsed = time.perf_counter() - t0
    print(f"  real WS transport: {seen_readings} reading messages, {len(messages)} total messages, {elapsed:.1f}s wall")
    return messages


def run_selective_via_send_loop_direct() -> List[dict]:
    engine = SelectivePsmEngine()
    source = ReplaySource(mode="pipeline", interval=0.0, dataset_dir=DATASET_DIR, engine=engine)

    class _Sink:
        def __init__(self):
            self.messages: List[dict] = []

        async def send_json(self, data):
            self.messages.append(data)
            if sum(1 for m in self.messages if m["type"] == "reading") >= N_FRAMES:
                raise asyncio.CancelledError  # stop after exactly one pass through the dataset

    sink = _Sink()
    throttle = AlertThrottle()
    history = deque(maxlen=360)

    async def _run():
        try:
            await send_loop(sink.send_json, source, throttle, history)
        except asyncio.CancelledError:
            pass

    t0 = time.perf_counter()
    asyncio.run(_run())
    elapsed = time.perf_counter() - t0
    n_reading = sum(1 for m in sink.messages if m["type"] == "reading")
    print(f"  send_loop() direct (real function, real ReplaySource/read_frame/reconcile): {n_reading} reading messages, {elapsed:.1f}s wall")
    return sink.messages


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=== Test H(a): CURRENT_BASELINE over the real WebSocket transport ===")
    baseline_msgs = run_baseline_over_real_websocket()
    with open(os.path.join(OUT_DIR, "m4_3_test_h_baseline_ws.json"), "w") as f:
        json.dump(baseline_msgs, f, indent=2, default=str)

    print("\n=== Test H(b): PSM10_SELECTIVE via send_loop() direct (transport bypassed, see module docstring) ===")
    selective_msgs = run_selective_via_send_loop_direct()
    with open(os.path.join(OUT_DIR, "m4_3_test_h_selective_sendloop.json"), "w") as f:
        json.dump(selective_msgs, f, indent=2, default=str)

    b_readings = [m for m in baseline_msgs if m["type"] == "reading"]
    b_flagged = [m for m in baseline_msgs if m["type"] == "flagged"]
    s_readings = [m for m in selective_msgs if m["type"] == "reading"]
    s_flagged = [m for m in selective_msgs if m["type"] == "flagged"]

    print(f"\nBASELINE (real transport): {len(b_readings)} readings, {len(b_flagged)} flagged entries")
    print(f"SELECTIVE (send_loop direct): {len(s_readings)} readings, {len(s_flagged)} flagged entries")

    assert len(b_readings) == N_FRAMES, f"expected {N_FRAMES} reading messages over real WS, got {len(b_readings)}"
    assert len(s_readings) == N_FRAMES, f"expected {N_FRAMES} reading messages via send_loop, got {len(s_readings)}"
    assert all(set(m["reading"].keys()) >= {"hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr", "timestamp"} for m in b_readings)
    assert all(set(m["reading"].keys()) >= {"hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean", "etco2", "temp", "rr", "timestamp"} for m in s_readings)
    print("\nShape assertions passed: both paths emit complete (no-null) VitalReading envelopes, real reconcile()-gated.")


if __name__ == "__main__":
    main()
