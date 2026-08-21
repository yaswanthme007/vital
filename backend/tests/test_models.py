from app.models import (
    AlarmLimit,
    ArchivedSession,
    Alert,
    AuditEntry,
    CalibrationProfile,
    DrugEvent,
    FlaggedReading,
    Patient,
    Session,
    SessionNote,
    VitalReading,
    VitalSummary,
)


def test_vital_reading_camel_keys():
    reading = VitalReading(
        hr=74,
        spo2=97,
        nibp_systolic=128,
        nibp_diastolic=82,
        nibp_mean=97,
        etco2=34,
        temp=36.8,
        rr=14,
        timestamp=1000,
        confidence=91.5,
        provenance="ai_high",
        per_vital_confidence={"hr": 95.0},
    )
    data = reading.model_dump(by_alias=True)
    assert set(data.keys()) == {
        "hr",
        "spo2",
        "nibpSystolic",
        "nibpDiastolic",
        "nibpMean",
        "etco2",
        "temp",
        "rr",
        "timestamp",
        "confidence",
        "provenance",
        "perVitalConfidence",
    }


def test_alarm_limit_camel_keys():
    limit = AlarmLimit(vital_type="hr", high_critical=130, high_warning=110, low_warning=50, low_critical=40)
    data = limit.model_dump(by_alias=True)
    assert set(data.keys()) == {"vitalType", "highCritical", "highWarning", "lowWarning", "lowCritical"}


def test_patient_and_session_note_instantiate():
    patient = Patient(id="PT-1", age=45, weight=70, asa=2)
    note = SessionNote(id="N-1", text="note", timestamp=1000, category="observation")
    assert patient.model_dump(by_alias=True)["asa"] == 2
    assert note.model_dump(by_alias=True)["category"] == "observation"


def test_session_camel_keys():
    patient = Patient(id="PT-1", age=45, weight=70, asa=2)
    session = Session(
        id="S-1",
        patient=patient,
        procedure="Lap Chole",
        anesthetist="Dr. Smith",
        start_time=1000,
        end_time=2000,
        notes=[],
        status="active",
        signed_at=None,
        archived_at=None,
        interrupted_at=None,
        current_owner="Dr. Smith",
        signed_by=None,
        signature_method=None,
        pdf_url=None,
        vitals_count=10,
        drugs_count=2,
        events_count=3,
        flagged_count=1,
    )
    data = session.model_dump(by_alias=True)
    assert set(data.keys()) == {
        "id",
        "patient",
        "procedure",
        "anesthetist",
        "startTime",
        "endTime",
        "notes",
        "status",
        "signedAt",
        "archivedAt",
        "interruptedAt",
        "currentOwner",
        "signedBy",
        "signatureMethod",
        "pdfUrl",
        "vitalsCount",
        "drugsCount",
        "eventsCount",
        "flaggedCount",
    }


def test_archived_session_instantiate():
    patient = Patient(id="PT-1")
    session = ArchivedSession(
        id="S-1",
        patient=patient,
        procedure="Lap Chole",
        anesthetist="Dr. Smith",
        start_time=1000,
        notes=[],
        status="completed",
        vital_summary=VitalSummary(avg_hr=75, min_spo2=94, avg_etco2=35, duration_min=60),
    )
    data = session.model_dump(by_alias=True)
    assert "vitalSummary" in data
    assert set(data["vitalSummary"].keys()) == {"avgHr", "minSpo2", "avgEtco2", "durationMin"}


def test_alert_camel_keys():
    alert = Alert(
        id="ALERT-1",
        vital_type="hr",
        severity="critical",
        message="HR high",
        value=142,
        unit="bpm",
        timestamp=1000,
        acknowledged=False,
    )
    data = alert.model_dump(by_alias=True)
    assert set(data.keys()) == {
        "id",
        "vitalType",
        "severity",
        "message",
        "value",
        "unit",
        "timestamp",
        "acknowledged",
    }


def test_flagged_reading_camel_keys():
    flagged = FlaggedReading(
        id="FLAG-1",
        timestamp=1000,
        vital="hr",
        ai_value="142",
        suggested_value="74",
        unit="bpm",
        confidence=58,
        severity="critical",
        status="pending",
        corrected_value=None,
        frame_note="note",
    )
    data = flagged.model_dump(by_alias=True)
    assert set(data.keys()) == {
        "id",
        "timestamp",
        "vital",
        "aiValue",
        "suggestedValue",
        "unit",
        "confidence",
        "severity",
        "status",
        "correctedValue",
        "frameNote",
    }


def test_audit_entry_camel_keys():
    entry = AuditEntry(
        id="AUD-1",
        timestamp=1000,
        action="ai_detect",
        vital="hr",
        value="142",
        prev_value=None,
        author="AI Vision",
        confidence=58,
    )
    data = entry.model_dump(by_alias=True)
    assert set(data.keys()) == {
        "id",
        "timestamp",
        "action",
        "vital",
        "value",
        "prevValue",
        "author",
        "confidence",
    }


def test_drug_event_camel_keys():
    event = DrugEvent(
        id="DRUG-1",
        session_id="S-1",
        drug_name="Propofol",
        dose=200,
        unit="mg",
        route="IV",
        rate=None,
        rate_unit=None,
        administered_at=1000,
        recorded_at=1001,
        notes=None,
        is_reversal=False,
        reversal_of=None,
        entry_method="quick_preset",
        entered_by="Dr. Smith",
        cumulative_dose=200,
    )
    data = event.model_dump(by_alias=True)
    assert set(data.keys()) == {
        "id",
        "sessionId",
        "drugName",
        "dose",
        "unit",
        "route",
        "rate",
        "rateUnit",
        "administeredAt",
        "recordedAt",
        "notes",
        "isReversal",
        "reversalOf",
        "entryMethod",
        "enteredBy",
        "cumulativeDose",
    }


def test_calibration_profile_instantiate():
    """M5.2 superseded this shape: `homography`/`color_map` (never
    referenced by anything — see docs/ARCHITECTURE.md's audit of the
    pre-M5.2 model) are gone, `roi_boxes` values are now NormalizedBox
    objects (not raw [x,y,w,h] pixel lists — M5.2 stores boxes normalized
    so a profile survives a resolution change, see
    app.models.calibration.NormalizedBox), and reference_width/height +
    field_meta + updated_at + is_active were added. See
    docs/M5_2_REAL_CALIBRATION_REPORT.md sec 4 for the full schema
    rationale. This test's job — pin the wire shape so a future milestone
    can't silently change it without updating this assertion — is
    unchanged; only the pinned shape itself is updated.

    M5.3 extends it again, by exactly two fields: `hasReferenceFrame` and
    `referenceFrameSha256`. Both describe the reference frame that
    app.pipeline.layout_tracker re-anchors the calibrated boxes to, and both
    were added only once that consumer existed — the condition the M5.2
    model docstring set for adding anything here, after ARCHITECTURE.md
    found the pre-M5.2 `homography` field referenced by nothing. Note what
    is still absent: no homography, no keypoints, no descriptors. The anchor
    is the reference IMAGE, stored in its own table
    (app.db.models.CalibrationReferenceFrameRow) and re-featurised on load;
    see docs/M5_3_LAYOUT_TRACKING_REPORT.md for that tradeoff. This
    assertion is updated, never loosened."""
    profile = CalibrationProfile(
        id="CAL-1",
        theatre_id="T-1",
        camera_id="CAM-1",
        layout_id="LAYOUT-1",
        reference_width=1280,
        reference_height=720,
        roi_boxes={"hr": {"x": 0.5, "y": 0.05, "w": 0.15, "h": 0.1}},
        field_meta={"hr": {"verified": True, "verified_value": "74", "verified_confidence": 95.0}},
        created_at=1000,
        updated_at=1000,
    )
    data = profile.model_dump(by_alias=True)
    assert set(data.keys()) == {
        "id",
        "theatreId",
        "cameraId",
        "layoutId",
        "version",
        "referenceWidth",
        "referenceHeight",
        "roiBoxes",
        "fieldMeta",
        "createdAt",
        "updatedAt",
        "isActive",
        "hasReferenceFrame",
        "referenceFrameSha256",
    }
    assert data["roiBoxes"]["hr"] == {"x": 0.5, "y": 0.05, "w": 0.15, "h": 0.1}


def test_populate_by_name_accepts_snake_case_input():
    reading = VitalReading(
        hr=74,
        spo2=97,
        nibp_systolic=128,
        nibp_diastolic=82,
        nibp_mean=97,
        etco2=34,
        temp=36.8,
        rr=14,
        timestamp=1000,
        confidence=91.5,
        provenance="ai_high",
    )
    assert reading.nibp_systolic == 128


def test_populate_by_alias_accepts_camel_case_input():
    reading = VitalReading(
        hr=74,
        spo2=97,
        nibpSystolic=128,
        nibpDiastolic=82,
        nibpMean=97,
        etco2=34,
        temp=36.8,
        rr=14,
        timestamp=1000,
        confidence=91.5,
        provenance="ai_high",
    )
    assert reading.nibp_systolic == 128
