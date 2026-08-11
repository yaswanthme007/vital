from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# Timestamps are stored as epoch-millisecond integers throughout, matching
# the frontend's Date.now()-based VitalReading/Session/Alert timestamps.


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Patient has no independent identity outside a session in this app, so
    # its fields are flattened onto the session row rather than a join.
    patient_id: Mapped[str] = mapped_column(String)
    patient_age: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    patient_weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    patient_asa: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    procedure: Mapped[str] = mapped_column(String)
    anesthetist: Mapped[str] = mapped_column(String)
    start_time: Mapped[int] = mapped_column(Integer)
    end_time: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")

    # Additive fields mirrored from app/models/session.py's Session model.
    signed_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    archived_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    interrupted_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_owner: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    signed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    signature_method: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pdf_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    vitals_count: Mapped[int] = mapped_column(Integer, default=0)
    drugs_count: Mapped[int] = mapped_column(Integer, default=0)
    events_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_count: Mapped[int] = mapped_column(Integer, default=0)

    # Populated only by end_session(); None until then.
    vital_summary_avg_hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vital_summary_min_spo2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vital_summary_avg_etco2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vital_summary_duration_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class SessionNoteRow(Base):
    __tablename__ = "session_notes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    text: Mapped[str] = mapped_column(String)
    timestamp: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String)


class VitalReadingRow(Base):
    __tablename__ = "vital_readings"
    __table_args__ = (Index("ix_vital_readings_session_timestamp", "session_id", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)

    hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spo2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nibp_systolic: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nibp_diastolic: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nibp_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    etco2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    temp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timestamp: Mapped[int] = mapped_column(Integer)

    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    provenance: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    per_vital_confidence: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    vital_type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    timestamp: Mapped[int] = mapped_column(Integer)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class AlarmLimitRow(Base):
    __tablename__ = "alarm_limits"
    __table_args__ = (UniqueConstraint("session_id", "vital_type", name="uq_alarm_limits_session_vital"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    vital_type: Mapped[str] = mapped_column(String)
    high_critical: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    high_warning: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low_warning: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low_critical: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class FlaggedReadingRow(Base):
    __tablename__ = "flagged_readings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    timestamp: Mapped[int] = mapped_column(Integer)
    vital: Mapped[str] = mapped_column(String)
    ai_value: Mapped[str] = mapped_column(String)
    suggested_value: Mapped[str] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    corrected_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    frame_note: Mapped[str] = mapped_column(String)


class AuditEntryRow(Base):
    __tablename__ = "audit_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    # Internal-only linkage so /api/flagged/{id}/... can find its trail —
    # NOT exposed via the Pydantic AuditEntry response (that model's shape
    # is unchanged), purely a DB-side bookkeeping column.
    flagged_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("flagged_readings.id"), nullable=True, index=True)
    timestamp: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String)
    vital: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    prev_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    author: Mapped[str] = mapped_column(String)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Audit is append-only by construction: no repo function ever
    # UPDATEs/DELETEs a row in this table.


class DrugEventRow(Base):
    __tablename__ = "drug_events"
    __table_args__ = (Index("ix_drug_events_session_administered", "session_id", "administered_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    drug_name: Mapped[str] = mapped_column(String)
    dose: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String)
    route: Mapped[str] = mapped_column(String)
    rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rate_unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    administered_at: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[int] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False)
    reversal_of: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    entry_method: Mapped[str] = mapped_column(String)
    entered_by: Mapped[str] = mapped_column(String)
    cumulative_dose: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Append-only correction history — NOT modeled as app.models.review's
    # AuditEntry, since that model's `vital` field is strictly typed to the 6
    # VitalType values (hr/spo2/nibp/etco2/temp/rr) and can't represent a
    # drug-dose/time correction without changing that Pydantic model's shape
    # (forbidden this session). Internal-only, not exposed via the DrugEvent
    # response. Each entry: {timestamp, field, prevValue, newValue, author}.
    correction_history: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
