from typing import List, Literal, Optional

from app.models.base import CamelModel

AsaClass = Literal[1, 2, 3, 4, 5, 6]

SessionStatus = Literal["active", "paused", "completed"]

SessionNoteCategory = Literal["drug", "event", "observation", "alarm"]


class Patient(CamelModel):
    id: str
    age: Optional[float] = None
    weight: Optional[float] = None
    asa: Optional[AsaClass] = None


class SessionNote(CamelModel):
    id: str
    text: str
    timestamp: float
    category: SessionNoteCategory


class SessionFormData(CamelModel):
    """Mirrors frontend-reference/src/types/session.ts's SessionFormData —
    not part of S0's original model list, added here since S7's POST
    /api/sessions needs this exact request-body shape."""

    patient_id: str
    patient_age: Optional[float] = None
    patient_weight: Optional[float] = None
    asa: Optional[AsaClass] = None
    procedure: str
    anesthetist: str


class Session(CamelModel):
    id: str
    patient: Patient
    procedure: str
    anesthetist: str
    start_time: float
    end_time: Optional[float] = None
    notes: List[SessionNote] = []
    status: SessionStatus

    # Additive (not present in frontend TS type)
    signed_at: Optional[float] = None
    archived_at: Optional[float] = None
    interrupted_at: Optional[float] = None
    current_owner: Optional[str] = None
    signed_by: Optional[str] = None
    signature_method: Optional[str] = None
    pdf_url: Optional[str] = None
    vitals_count: int = 0
    drugs_count: int = 0
    events_count: int = 0
    flagged_count: int = 0


class VitalSummary(CamelModel):
    avg_hr: float
    min_spo2: float
    avg_etco2: float
    duration_min: float


class ObservationStats(CamelModel):
    """M5.7. Replaces Archive's previously-hardcoded 'AI Processing' panel
    ('Frames processed 3,847' / 'Avg confidence 94.2%') with real numbers
    computed from this session's own persisted app.db.models.VitalReadingRow
    rows -- see app.db.repo._observation_stats. Every row counted here is
    already confirmed-only post-M5.7 (app.ws.vitals.send_loop only persists
    fields reconcile() marked 'confirmed'), so `confirmed_observations` is
    not a filtered subset of `readings_count` -- it IS readings_count,
    reported under its own name so a reader never has to know that fact to
    trust the number."""

    readings_count: int = 0
    confirmed_observations: int = 0
    avg_confidence: Optional[float] = None
    source: Optional[Literal["camera", "synthetic", "replay", "mixed"]] = None


class ArchivedSession(Session):
    vital_summary: VitalSummary
    # Additive (not present in frontend TS type)
    observation_stats: ObservationStats = ObservationStats()
