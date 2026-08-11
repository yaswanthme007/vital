from typing import Dict, Literal, Optional

from pydantic import Field

from app.models.base import CamelModel

VitalType = Literal["hr", "spo2", "nibp", "etco2", "temp", "rr"]

AlarmSeverity = Literal["normal", "warning", "critical", "off"]

Provenance = Literal[
    "ai_high",
    "ai_medium",
    "ai_low",
    "human_confirmed",
    "human_corrected",
]


class VitalReading(CamelModel):
    hr: float
    spo2: float
    nibp_systolic: float
    nibp_diastolic: float
    nibp_mean: float
    etco2: float
    temp: float
    rr: float
    timestamp: float

    # Additive (not present in frontend TS type)
    confidence: float = Field(ge=0, le=100)
    provenance: Provenance
    per_vital_confidence: Optional[Dict[str, float]] = None


class AlarmLimit(CamelModel):
    vital_type: VitalType
    high_critical: Optional[float] = None
    high_warning: Optional[float] = None
    low_warning: Optional[float] = None
    low_critical: Optional[float] = None
