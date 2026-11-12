import random
import time
from typing import Dict, List, Optional

# Kept in lock-step with the frontend's src/lib/alertRules.ts checkAlerts().
# Same branches, same thresholds, same message text, same else-if
# exclusivity (e.g. HR can only ever fire ONE of its 4 branches per check).
#
# NIBP and RR alerts were added on both sides together: before, the two
# vitals had alarm ranges defined (src/types/vitals.ts DEFAULT_ALARM_LIMITS
# colours the tile) but never raised an alert, so intra-operative
# hypotension -- the commonest haemodynamic event under anaesthesia -- was
# silently missed. Thresholds match DEFAULT_ALARM_LIMITS, plus the usual
# MAP floor of 65 mmHg (warning) / 55 mmHg (critical).
THROTTLE_WINDOW_MS = 30_000

MAP_CRITICAL_LOW = 55
MAP_WARNING_LOW = 65
SBP_CRITICAL_LOW = 70
SBP_WARNING_LOW = 90
SBP_WARNING_HIGH = 160
SBP_CRITICAL_HIGH = 180

RR_CRITICAL_HIGH = 30
RR_WARNING_HIGH = 24
RR_WARNING_LOW = 8
RR_CRITICAL_LOW = 5


def _nibp_alert(reading: dict) -> Optional[dict]:
    """At most ONE NIBP alert per check. Low pressure outranks high, and MAP
    outranks systolic when both are present (MAP is what perfuses organs).
    Either sub-field may be None (not confirmed this tick) and is skipped."""
    mean = reading.get("nibpMean")
    sbp = reading.get("nibpSystolic")

    def alert(severity: str, message: str, value: float) -> dict:
        return {"vitalType": "nibp", "severity": severity, "message": message, "value": value, "unit": "mmHg"}

    if mean is not None and mean <= MAP_CRITICAL_LOW:
        return alert("critical", "MAP CRITICALLY LOW", mean)
    if sbp is not None and sbp <= SBP_CRITICAL_LOW:
        return alert("critical", "Systolic BP CRITICALLY LOW", sbp)
    if mean is not None and mean <= MAP_WARNING_LOW:
        return alert("warning", "Hypotension (MAP)", mean)
    if sbp is not None and sbp <= SBP_WARNING_LOW:
        return alert("warning", "Hypotension (Systolic)", sbp)
    if sbp is not None and sbp >= SBP_CRITICAL_HIGH:
        return alert("critical", "Systolic BP CRITICALLY HIGH", sbp)
    if sbp is not None and sbp >= SBP_WARNING_HIGH:
        return alert("warning", "Hypertension", sbp)
    return None


def check_alerts(reading: dict) -> List[dict]:
    """reading: VitalReading-shaped dict (camelCase keys). Returns a list of
    NewAlert-shaped dicts — {vitalType, severity, message, value, unit} —
    WITHOUT id/timestamp/acknowledged, mirroring the frontend's split between
    checkAlerts() (decides WHAT to alert) and alertStore.addAlert() (decides
    id/timestamp). Values that are None (a vital the OCR pipeline couldn't
    read) are skipped rather than compared — the frontend's synthetic
    readings never have this case, but ours can."""
    alerts: List[dict] = []

    spo2 = reading.get("spo2")
    if spo2 is not None:
        if spo2 <= 90:
            alerts.append({"vitalType": "spo2", "severity": "critical", "message": "SpO₂ CRITICALLY LOW", "value": spo2, "unit": "%"})
        elif spo2 <= 94:
            alerts.append({"vitalType": "spo2", "severity": "warning", "message": "SpO₂ Low", "value": spo2, "unit": "%"})

    hr = reading.get("hr")
    if hr is not None:
        if hr >= 130:
            alerts.append({"vitalType": "hr", "severity": "critical", "message": "Heart Rate HIGH", "value": hr, "unit": "bpm"})
        elif hr >= 110:
            alerts.append({"vitalType": "hr", "severity": "warning", "message": "Heart Rate Elevated", "value": hr, "unit": "bpm"})
        elif hr <= 40:
            alerts.append({"vitalType": "hr", "severity": "critical", "message": "Heart Rate CRITICALLY LOW", "value": hr, "unit": "bpm"})
        elif hr <= 50:
            alerts.append({"vitalType": "hr", "severity": "warning", "message": "Heart Rate Low", "value": hr, "unit": "bpm"})

    etco2 = reading.get("etco2")
    if etco2 is not None:
        if etco2 >= 55:
            alerts.append({"vitalType": "etco2", "severity": "critical", "message": "EtCO₂ CRITICALLY HIGH", "value": etco2, "unit": "mmHg"})
        elif etco2 >= 50:
            alerts.append({"vitalType": "etco2", "severity": "warning", "message": "EtCO₂ Elevated", "value": etco2, "unit": "mmHg"})

    temp = reading.get("temp")
    if temp is not None:
        if temp >= 39.5:
            alerts.append({"vitalType": "temp", "severity": "critical", "message": "Hyperthermia", "value": temp, "unit": "°C"})
        elif temp >= 38.5:
            alerts.append({"vitalType": "temp", "severity": "warning", "message": "Fever", "value": temp, "unit": "°C"})
        elif temp <= 35.5:
            alerts.append({"vitalType": "temp", "severity": "warning", "message": "Hypothermia Risk", "value": temp, "unit": "°C"})

    nibp = _nibp_alert(reading)
    if nibp is not None:
        alerts.append(nibp)

    rr = reading.get("rr")
    if rr is not None:
        if rr >= RR_CRITICAL_HIGH:
            alerts.append({"vitalType": "rr", "severity": "critical", "message": "Resp Rate HIGH", "value": rr, "unit": "/min"})
        elif rr >= RR_WARNING_HIGH:
            alerts.append({"vitalType": "rr", "severity": "warning", "message": "Resp Rate Elevated", "value": rr, "unit": "/min"})
        elif rr <= RR_CRITICAL_LOW:
            alerts.append({"vitalType": "rr", "severity": "critical", "message": "Resp Rate CRITICALLY LOW", "value": rr, "unit": "/min"})
        elif rr <= RR_WARNING_LOW:
            alerts.append({"vitalType": "rr", "severity": "warning", "message": "Resp Rate Low", "value": rr, "unit": "/min"})

    return alerts


def build_alert(data: dict) -> dict:
    """Fill in id/timestamp/acknowledged for a NewAlert-shaped dict from
    check_alerts(), matching alertStore.ts's addAlert() id format exactly:
    `ALERT-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`."""
    now_ms = int(time.time() * 1000)
    suffix = "".join(random.choices("0123456789abcdefghijklmnopqrstuvwxyz", k=4))
    return {
        **data,
        "id": f"ALERT-{now_ms}-{suffix}",
        "timestamp": now_ms,
        "acknowledged": False,
    }


class AlertThrottle:
    """Port of the hook's `lastAlertAt` ref + 30s per-key throttle. One
    instance per active stream/session — matches the hook's ref living for
    the component's mount lifetime, resetting fresh each session."""

    def __init__(self, window_ms: float = THROTTLE_WINDOW_MS):
        self.window_ms = window_ms
        self._last_alert_at: Dict[str, float] = {}

    def should_emit(self, vital_type: str, severity: str, now_ms: Optional[float] = None) -> bool:
        now_ms = now_ms if now_ms is not None else time.time() * 1000
        key = f"{vital_type}-{severity}"
        last = self._last_alert_at.get(key, 0)
        if now_ms - last > self.window_ms:
            self._last_alert_at[key] = now_ms
            return True
        return False
