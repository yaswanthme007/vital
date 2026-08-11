import asyncio
import random
from collections import deque

import pytest
from fastapi.testclient import TestClient

from app.alerts.rules import AlertThrottle, build_alert, check_alerts
from app.main import app
from app.sources.base import Frame, VitalsSource
from app.sources.replay import VITALS, _build_reading

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


def _receive_until(ws, msg_type: str, max_tries: int = 20) -> dict:
    """Reading/alert envelopes can interleave with a message we're waiting
    for (e.g. an ack) — skip past them rather than assuming strict ordering."""
    for _ in range(max_tries):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"did not receive a '{msg_type}' message within {max_tries} tries")


# ─── WS endpoint: reading envelope shape ─────────────────────────────────────


def test_ws_streams_readings_with_camel_case_keys():
    client = TestClient(app)
    with client.websocket_connect("/ws/vitals/test?source=synthetic&interval=0.01&seed=1") as ws:
        for _ in range(3):
            msg = ws.receive_json()
            assert msg["type"] == "reading"
            assert set(msg["reading"].keys()) == VITAL_READING_KEYS
            assert set(msg["confidence"].keys()) == set(VITALS)
            assert msg["provenance"] == "ai_high"
            assert all(v == 100.0 for v in msg["confidence"].values())


def test_ws_trigger_nibp_and_ack_alert_stubs_respond():
    client = TestClient(app)
    with client.websocket_connect("/ws/vitals/test2?source=synthetic&interval=0.01") as ws:
        ws.send_json({"type": "trigger_nibp"})
        nibp_msg = _receive_until(ws, "nibp_measuring")
        assert nibp_msg == {"type": "nibp_measuring"}

        ws.send_json({"type": "ack_alert", "id": "ALERT-123"})
        ack_msg = _receive_until(ws, "alert_acknowledged")
        assert ack_msg == {"type": "alert_acknowledged", "id": "ALERT-123"}


def test_ws_unknown_pipeline_dataset_reports_error_and_closes():
    client = TestClient(app)
    with client.websocket_connect("/ws/vitals/test3?source=pipeline&dataset=simulator/out/does_not_exist") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"


# ─── Synthetic reading shape/ranges match the frontend's buildReading ───────


def test_synthetic_build_reading_matches_frontend_shape_and_ranges():
    rng = random.Random(7)
    prev = None
    for i in range(60):
        reading = _build_reading(rng, prev, elapsed=float(i), timestamp_ms=1_700_000_000_000 + i * 1000)
        assert set(reading.keys()) == VITAL_READING_KEYS
        assert 45 <= reading["hr"] <= 135
        assert 88 <= reading["spo2"] <= 100
        assert 18 <= reading["etco2"] <= 65
        assert 34 <= reading["temp"] <= 40
        assert 4 <= reading["rr"] <= 35
        # hr/spo2/rr/nibpSystolic/nibpDiastolic/nibpMean are whole numbers;
        # temp/etco2 keep exactly one decimal — matches Math.round semantics.
        for key in ("hr", "spo2", "rr", "nibpSystolic", "nibpDiastolic", "nibpMean"):
            assert reading[key] == int(reading[key])
        assert round(reading["temp"], 1) == reading["temp"]
        assert round(reading["etco2"], 1) == reading["etco2"]
        prev = reading


# ─── Alert rules: exact threshold/message/severity port ─────────────────────


def test_check_alerts_matches_frontend_thresholds():
    normal = {"hr": 72, "spo2": 98, "etco2": 38, "temp": 36.8}

    assert check_alerts(normal) == []

    spo2_crit = check_alerts(dict(normal, spo2=90))
    assert spo2_crit == [{"vitalType": "spo2", "severity": "critical", "message": "SpO₂ CRITICALLY LOW", "value": 90, "unit": "%"}]

    spo2_warn = check_alerts(dict(normal, spo2=94))
    assert spo2_warn == [{"vitalType": "spo2", "severity": "warning", "message": "SpO₂ Low", "value": 94, "unit": "%"}]

    hr_crit_high = check_alerts(dict(normal, hr=130))
    assert hr_crit_high[0]["severity"] == "critical" and hr_crit_high[0]["message"] == "Heart Rate HIGH"

    hr_warn_high = check_alerts(dict(normal, hr=110))
    assert hr_warn_high[0]["severity"] == "warning" and hr_warn_high[0]["message"] == "Heart Rate Elevated"

    hr_crit_low = check_alerts(dict(normal, hr=40))
    assert hr_crit_low[0]["severity"] == "critical" and hr_crit_low[0]["message"] == "Heart Rate CRITICALLY LOW"

    hr_warn_low = check_alerts(dict(normal, hr=50))
    assert hr_warn_low[0]["severity"] == "warning" and hr_warn_low[0]["message"] == "Heart Rate Low"

    # else-if exclusivity: exactly one HR alert per check, never two.
    assert len(check_alerts(dict(normal, hr=200))) == 1

    etco2_crit = check_alerts(dict(normal, etco2=55))
    assert etco2_crit[0]["severity"] == "critical" and etco2_crit[0]["message"] == "EtCO₂ CRITICALLY HIGH"

    temp_crit = check_alerts(dict(normal, temp=39.5))
    assert temp_crit[0]["message"] == "Hyperthermia"

    temp_hypo = check_alerts(dict(normal, temp=35.5))
    assert temp_hypo[0]["message"] == "Hypothermia Risk"

    # rr/nibp are never checked, even far out of range — matches the hook exactly.
    assert check_alerts(dict(normal, rr=1, nibpSystolic=300)) == []


def test_check_alerts_skips_missing_values_without_crashing():
    reading = {"hr": None, "spo2": None, "etco2": None, "temp": None}
    assert check_alerts(reading) == []


def test_alert_throttle_window_matches_frontend_30s():
    throttle = AlertThrottle()
    base = 1_700_000_000_000

    assert throttle.should_emit("spo2", "critical", now_ms=base) is True
    assert throttle.should_emit("spo2", "critical", now_ms=base + 1_000) is False
    assert throttle.should_emit("spo2", "critical", now_ms=base + 29_999) is False
    assert throttle.should_emit("spo2", "critical", now_ms=base + 30_001) is True
    # a different severity is a different key, independently throttled.
    assert throttle.should_emit("spo2", "warning", now_ms=base + 1_000) is True


def test_build_alert_matches_alert_store_shape():
    data = check_alerts({"spo2": 88})[0]
    alert = build_alert(data)
    assert alert["id"].startswith("ALERT-")
    assert alert["acknowledged"] is False
    assert isinstance(alert["timestamp"], int)
    assert alert["vitalType"] == "spo2"
    assert alert["severity"] == "critical"


# ─── WS pipeline glue: send_loop emits + throttles alerts ───────────────────


class _FixedFrameSource(VitalsSource):
    def __init__(self, frames):
        self._frames = frames

    async def stream(self):
        for frame in self._frames:
            yield frame


class _RecordingSink:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def test_send_loop_emits_alert_and_throttles_duplicate():
    from app.ws.vitals import send_loop

    critical_reading = {
        "hr": 72,
        "spo2": 88,
        "nibpSystolic": 120,
        "nibpDiastolic": 78,
        "nibpMean": 92,
        "etco2": 38,
        "temp": 36.8,
        "rr": 14,
        "timestamp": 1_700_000_000_000,
    }
    confidence = {v: 100.0 for v in VITALS}
    frames = [
        Frame(reading=critical_reading, per_vital_confidence=confidence, provenance="ai_high"),
        Frame(reading=critical_reading, per_vital_confidence=confidence, provenance="ai_high"),
    ]

    source = _FixedFrameSource(frames)
    sink = _RecordingSink()
    throttle = AlertThrottle()
    history = deque(maxlen=10)

    asyncio.run(send_loop(sink.send_json, source, throttle, history))

    reading_msgs = [m for m in sink.messages if m["type"] == "reading"]
    alert_msgs = [m for m in sink.messages if m["type"] == "alert"]

    assert len(reading_msgs) == 2
    # Both frames are identically critical, arriving well within 30s of each
    # other — only the FIRST should produce an alert; the second is throttled.
    assert len(alert_msgs) == 1
    alert = alert_msgs[0]["alert"]
    assert alert["severity"] == "critical"
    assert alert["vitalType"] == "spo2"
    assert alert["message"] == "SpO₂ CRITICALLY LOW"
    assert alert["acknowledged"] is False
    assert len(history) == 2
