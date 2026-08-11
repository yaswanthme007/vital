import math
import random
import time
from typing import List, Optional

# Mirrors frontend-reference/src/hooks/useVitalsSimulation.ts (buildReading) and
# frontend-reference/src/lib/utils.ts (randomWalk), so simulated drift matches
# the ranges/behaviour the frontend itself considers realistic.


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _js_round(value: float) -> int:
    """Match JS Math.round (half rounds toward +Infinity) for our
    non-negative vitals, rather than Python's round-half-to-even."""
    return math.floor(value + 0.5)


def _random_walk(rng: random.Random, current: float, step: float, lo: float, hi: float, target: float, pull: float = 0.05) -> float:
    delta = (rng.random() - 0.5) * step * 2 + (target - current) * pull
    return _clamp(current + delta, lo, hi)


def _build_reading(rng: random.Random, prev: Optional[dict], elapsed: float, timestamp_ms: int) -> dict:
    base_hr = 72 + 6 * math.sin((elapsed / 900) * math.pi)
    base_spo2 = 98.5
    base_etco2 = 38

    hr = _random_walk(rng, prev["hr"], 1.5, 45, 135, base_hr, 0.08) if prev else base_hr
    spo2 = _random_walk(rng, prev["spo2"], 0.4, 88, 100, base_spo2, 0.12) if prev else base_spo2
    etco2 = _random_walk(rng, prev["etco2"], 0.8, 18, 65, base_etco2, 0.08) if prev else base_etco2

    systolic = 118 + 6 * math.sin((elapsed / 600) * math.pi) + (rng.random() - 0.5) * 6
    diastolic = 74 + 3 * math.sin((elapsed / 800) * math.pi) + (rng.random() - 0.5) * 4
    nibp_mean = _js_round(diastolic + (systolic - diastolic) / 3)

    temp = _random_walk(rng, prev["temp"], 0.05, 34, 40, 36.8, 0.02) if prev else 36.8
    rr = _random_walk(rng, prev["rr"], 0.5, 4, 35, 14, 0.06) if prev else 14

    return {
        "hr": _js_round(hr),
        "spo2": _js_round(spo2),
        "nibpSystolic": _js_round(systolic),
        "nibpDiastolic": _js_round(diastolic),
        "nibpMean": nibp_mean,
        "etco2": _js_round(etco2 * 10) / 10,
        "temp": _js_round(temp * 10) / 10,
        "rr": _js_round(rr),
        "timestamp": timestamp_ms,
    }


def generate_vitals_series(
    duration_s: int,
    interval_s: float = 1.0,
    seed: Optional[int] = None,
    start_time_ms: Optional[int] = None,
) -> List[dict]:
    """A physiologically-plausible drifting sequence of VitalReading dicts
    over duration_s seconds, one reading every interval_s seconds.

    Each reading follows the frontend's VitalReading shape (camelCase keys).
    Pass `seed` for a reproducible series.
    """
    rng = random.Random(seed)
    start_time_ms = start_time_ms if start_time_ms is not None else int(time.time() * 1000)

    n_steps = max(1, int(round(duration_s / interval_s)))
    readings: List[dict] = []
    prev = None
    for i in range(n_steps):
        elapsed = i * interval_s
        timestamp_ms = start_time_ms + int(elapsed * 1000)
        reading = _build_reading(rng, prev, elapsed, timestamp_ms)
        readings.append(reading)
        prev = reading
    return readings
