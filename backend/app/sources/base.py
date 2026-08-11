from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional


@dataclass
class Frame:
    """One tick from a VitalsSource.

    reading: VitalReading-shaped dict (camelCase) — hr, spo2, nibpSystolic,
      nibpDiastolic, nibpMean, etco2, temp, rr, timestamp. Any field the
      source couldn't produce is None (matches app.pipeline.read_frame's
      contract for pipeline-backed sources).
    per_vital_confidence: {vital: 0-100} for hr/spo2/nibp/etco2/temp/rr.
    provenance: a Provenance literal from app.models.vitals (e.g. "ai_high").
    source_frame_id: optional identifier for the underlying frame (e.g. a
      simulator sample id) — useful for debugging/replay, not required.
    """

    reading: dict
    per_vital_confidence: Dict[str, float] = field(default_factory=dict)
    provenance: str = "ai_high"
    source_frame_id: Optional[str] = None


class VitalsSource(ABC):
    """Swappable vitals feed. app/ws/vitals.py only depends on this
    interface — S11's real-camera/OCR source and this session's
    ReplaySource (synthetic + pipeline modes) both implement just
    `stream()`, so the WebSocket layer never changes when the source does."""

    @abstractmethod
    async def stream(self) -> AsyncIterator[Frame]:
        """Yield Frames indefinitely (or until the caller stops iterating).
        Implementations should `await asyncio.sleep(...)` between ticks
        rather than yielding as fast as possible."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for type checkers
