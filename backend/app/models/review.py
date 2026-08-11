from typing import Literal, Optional

from app.models.base import CamelModel
from app.models.vitals import VitalType

FlaggedSeverity = Literal["critical", "warning"]

FlaggedStatus = Literal["pending", "corrected", "dismissed"]

AuditAction = Literal["ai_detect", "human_correct", "human_dismiss"]


class FlaggedReading(CamelModel):
    id: str
    timestamp: float
    vital: VitalType
    ai_value: str
    suggested_value: str
    unit: str
    confidence: float
    severity: FlaggedSeverity
    status: FlaggedStatus
    corrected_value: Optional[str] = None
    frame_note: str


class AuditEntry(CamelModel):
    id: str
    timestamp: float
    action: AuditAction
    vital: VitalType
    value: str
    prev_value: Optional[str] = None
    author: str
    confidence: Optional[float] = None
