from typing import Literal, Optional

from app.models.base import CamelModel

DrugEntryMethod = Literal["quick_preset", "manual", "voice"]


class DrugEvent(CamelModel):
    id: str
    session_id: str
    drug_name: str
    dose: float
    unit: str
    route: str
    rate: Optional[float] = None
    rate_unit: Optional[str] = None
    administered_at: float
    recorded_at: float
    notes: Optional[str] = None
    is_reversal: bool
    reversal_of: Optional[str] = None
    entry_method: DrugEntryMethod
    entered_by: str
    cumulative_dose: Optional[float] = None
