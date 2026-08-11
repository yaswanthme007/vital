from typing import Literal, Optional, Union

from app.models.base import CamelModel
from app.models.vitals import VitalType

AlertSeverity = Literal["critical", "warning", "info"]


class Alert(CamelModel):
    id: str
    vital_type: Union[VitalType, Literal["system"]]
    severity: AlertSeverity
    message: str
    value: Optional[float] = None
    unit: Optional[str] = None
    timestamp: float
    acknowledged: bool
