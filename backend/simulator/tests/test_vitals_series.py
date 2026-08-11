from simulator.vitals_series import generate_vitals_series

REQUIRED_KEYS = {
    "hr", "spo2", "nibpSystolic", "nibpDiastolic", "nibpMean",
    "etco2", "temp", "rr", "timestamp",
}


def test_series_length_and_keys():
    series = generate_vitals_series(duration_s=30, interval_s=1.0, seed=1)
    assert len(series) == 30
    for reading in series:
        assert set(reading.keys()) == REQUIRED_KEYS


def test_series_stays_in_realistic_ranges():
    series = generate_vitals_series(duration_s=180, interval_s=1.0, seed=7)
    for r in series:
        assert 45 <= r["hr"] <= 135
        assert 88 <= r["spo2"] <= 100
        assert 18 <= r["etco2"] <= 65
        assert 34 <= r["temp"] <= 40
        assert 4 <= r["rr"] <= 35
        assert 95 <= r["nibpSystolic"] <= 135
        assert 60 <= r["nibpDiastolic"] <= 90


def test_series_drifts_not_constant():
    # HR/EtCO2 have enough step size to visibly wander every run. SpO2/RR/Temp
    # use deliberately tiny step+pull constants (matching the frontend) and can
    # legitimately stay flat for a 60s window once rounded — that's realistic
    # monitor behaviour, not a bug, so we don't assert drift on those here.
    series = generate_vitals_series(duration_s=60, interval_s=1.0, seed=3)

    hr_values = {r["hr"] for r in series}
    etco2_values = {r["etco2"] for r in series}
    nibp_values = {(r["nibpSystolic"], r["nibpDiastolic"]) for r in series}

    assert len(hr_values) > 1, "HR should drift over a 60s series, not stay pinned"
    assert len(etco2_values) > 1, "EtCO2 should drift over a 60s series, not stay pinned"
    assert len(nibp_values) > 1, "NIBP should vary reading-to-reading, not stay pinned"


def test_series_is_reproducible_with_seed():
    a = generate_vitals_series(duration_s=10, interval_s=1.0, seed=99, start_time_ms=0)
    b = generate_vitals_series(duration_s=10, interval_s=1.0, seed=99, start_time_ms=0)
    assert a == b


def test_series_differs_across_seeds():
    a = generate_vitals_series(duration_s=10, interval_s=1.0, seed=1, start_time_ms=0)
    b = generate_vitals_series(duration_s=10, interval_s=1.0, seed=2, start_time_ms=0)
    assert a != b


def test_series_timestamps_increase_from_start():
    series = generate_vitals_series(duration_s=10, interval_s=1.0, seed=5, start_time_ms=1000)
    timestamps = [r["timestamp"] for r in series]
    assert timestamps == sorted(timestamps)
    assert timestamps[0] == 1000
    assert timestamps[-1] == 1000 + 9 * 1000
