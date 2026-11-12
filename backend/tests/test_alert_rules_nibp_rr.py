"""NIBP + RR alert rules. Same cases as src/lib/alertRules.test.ts -- the
backend and frontend rule sets must stay in lock-step."""
import pytest

from app.alerts.rules import check_alerts

NORMAL = {
    "hr": 72, "spo2": 98, "etco2": 38, "temp": 36.8,
    "nibpSystolic": 120, "nibpDiastolic": 80, "nibpMean": 93, "rr": 14,
}


def _only(vital, **overrides):
    return [a for a in check_alerts(dict(NORMAL, **overrides)) if a["vitalType"] == vital]


def test_fully_normal_reading_raises_nothing():
    assert check_alerts(NORMAL) == []


def test_absent_or_none_fields_are_skipped():
    assert check_alerts({}) == []
    assert check_alerts({"nibpMean": None, "nibpSystolic": None, "rr": None}) == []


def test_map_critical_low():
    assert _only("nibp", nibpMean=55) == [
        {"vitalType": "nibp", "severity": "critical", "message": "MAP CRITICALLY LOW", "value": 55, "unit": "mmHg"}
    ]


def test_map_hypotension_warning():
    assert _only("nibp", nibpMean=65) == [
        {"vitalType": "nibp", "severity": "warning", "message": "Hypotension (MAP)", "value": 65, "unit": "mmHg"}
    ]


def test_falls_back_to_systolic_when_map_not_confirmed():
    assert _only("nibp", nibpMean=None, nibpSystolic=70)[0]["message"] == "Systolic BP CRITICALLY LOW"
    assert _only("nibp", nibpMean=None, nibpSystolic=90)[0]["message"] == "Hypotension (Systolic)"


def test_critical_systolic_outranks_map_warning():
    (alert,) = _only("nibp", nibpMean=60, nibpSystolic=68)
    assert alert["severity"] == "critical"
    assert alert["value"] == 68


def test_hypertension_on_systolic():
    assert _only("nibp", nibpSystolic=160)[0]["message"] == "Hypertension"
    crit = _only("nibp", nibpSystolic=180)[0]
    assert crit["severity"] == "critical" and crit["message"] == "Systolic BP CRITICALLY HIGH"


def test_at_most_one_nibp_alert_per_check():
    assert len(_only("nibp", nibpMean=40, nibpSystolic=50)) == 1


@pytest.mark.parametrize(
    "rr,severity,message",
    [
        (30, "critical", "Resp Rate HIGH"),
        (24, "warning", "Resp Rate Elevated"),
        (8, "warning", "Resp Rate Low"),
        (5, "critical", "Resp Rate CRITICALLY LOW"),
        (0, "critical", "Resp Rate CRITICALLY LOW"),
    ],
)
def test_rr_thresholds(rr, severity, message):
    assert _only("rr", rr=rr) == [
        {"vitalType": "rr", "severity": severity, "message": message, "value": rr, "unit": "/min"}
    ]


def test_rr_normal_band_is_silent():
    assert _only("rr", rr=9) == []
    assert _only("rr", rr=23) == []
