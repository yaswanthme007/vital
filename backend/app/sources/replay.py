import asyncio
import math
import random
import time
from typing import AsyncIterator, List, Optional

import numpy as np
from PIL import Image

from app.eval.harness import load_dataset
from app.pipeline.ocr import OcrEngine
from app.pipeline.read_frame import read_frame
from app.sources.base import Frame, VitalsSource

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _js_round(value: float) -> int:
    """Match JS Math.round (half rounds toward +Infinity) for our
    non-negative vitals, rather than Python's round-half-to-even."""
    return math.floor(value + 0.5)


def _random_walk(rng: random.Random, current: float, step: float, lo: float, hi: float, target: float, pull: float) -> float:
    delta = (rng.random() - 0.5) * step * 2 + (target - current) * pull
    return _clamp(current + delta, lo, hi)


def _build_reading(rng: random.Random, prev: Optional[dict], elapsed: float, timestamp_ms: int) -> dict:
    """Literal port of useVitalsSimulation.ts's buildReading() — same
    constants, same rounding (JS Math.round semantics), same shape. Kept as
    the canonical LIVE version; simulator/vitals_series.py (S2) is a separate
    batch-generation port used only by the dev/data-gen tooling."""
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


def _provenance_for_confidence(mean_confidence: float) -> str:
    if mean_confidence >= 80:
        return "ai_high"
    if mean_confidence >= 50:
        return "ai_medium"
    return "ai_low"


class ReplaySource(VitalsSource):
    """Yields readings on an interval so the demo never depends on live
    hardware. Two modes, selected at construction:

    - "synthetic": buildReading() ported exactly from the frontend hook.
      provenance="ai_high", per_vital_confidence ~100 for every vital.
    - "pipeline": replays a simulator dataset dir (S3 --count output) through
      app.pipeline.read_frame.read_frame() (S4+S5, untouched) — real OCR
      reads with real per-vital confidence. Loops the dataset once exhausted.
    """

    def __init__(
        self,
        mode: str = "synthetic",
        interval: float = 1.0,
        dataset_dir: Optional[str] = None,
        seed: Optional[int] = None,
        engine: Optional[OcrEngine] = None,
    ):
        if mode not in ("synthetic", "pipeline"):
            raise ValueError(f"Unknown ReplaySource mode '{mode}'. Choose 'synthetic' or 'pipeline'.")
        if mode == "pipeline" and not dataset_dir:
            raise ValueError("ReplaySource(mode='pipeline') requires dataset_dir")

        self.mode = mode
        self.interval = interval
        self.dataset_dir = dataset_dir
        self.seed = seed
        self.engine = engine

        # Validated eagerly (not lazily inside the async generator) so
        # construction-time callers — e.g. the WS endpoint — can catch a bad
        # dataset_dir with a plain try/except around ReplaySource(...) rather
        # than needing to unwrap an exception raised mid-stream.
        self._samples: Optional[List[dict]] = None
        if mode == "pipeline":
            self._samples = load_dataset(dataset_dir)
            if not self._samples:
                raise ValueError(f"No sample_*.json/.png pairs found under {dataset_dir}")

    async def stream(self) -> AsyncIterator[Frame]:
        if self.mode == "synthetic":
            async for frame in self._stream_synthetic():
                yield frame
        else:
            async for frame in self._stream_pipeline():
                yield frame

    async def _stream_synthetic(self) -> AsyncIterator[Frame]:
        rng = random.Random(self.seed)
        start_time = time.time()
        prev: Optional[dict] = None
        confidence = {v: 100.0 for v in VITALS}

        while True:
            elapsed = time.time() - start_time
            reading = _build_reading(rng, prev, elapsed, int(time.time() * 1000))
            prev = reading
            yield Frame(reading=reading, per_vital_confidence=dict(confidence), provenance="ai_high")
            await asyncio.sleep(self.interval)

    async def _stream_pipeline(self) -> AsyncIterator[Frame]:
        samples = self._samples

        index = 0
        while True:
            sample = samples[index % len(samples)]
            img = np.array(Image.open(sample["png_path"]).convert("RGB"))
            reading, confidences = read_frame(img, engine=self.engine)
            reading["timestamp"] = int(time.time() * 1000)

            mean_confidence = sum(confidences.values()) / len(confidences) if confidences else 0.0
            provenance = _provenance_for_confidence(mean_confidence)

            yield Frame(
                reading=reading,
                per_vital_confidence=confidences,
                provenance=provenance,
                source_frame_id=sample["id"],
            )
            index += 1
            await asyncio.sleep(self.interval)
