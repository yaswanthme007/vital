"""Medico-legal PDF export, built with ReportLab. Assembles the same data
build_chart() aggregates (periodic vitals, drug log) plus the complete
correction history from BOTH audit sources — S8's audit_entries and S9's
per-drug-event correction_history — into one chronologically-sorted section,
and a signature block read from the session row (see repo.sign_session for
why the signature itself isn't an AuditEntry). Framed as "audit-grade,
clinician-confirmed" — deliberately not "court-admissible" or any claimed
device class."""

import io
from datetime import datetime, timezone
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session as OrmSession

from app.chart.assemble import build_chart
from app.db import repo

DISCLAIMER = "This is an audit-grade record, clinician-confirmed. Not a certified medical device."

_styles = getSampleStyleSheet()
_cell_style = ParagraphStyle("cell", parent=_styles["BodyText"], fontSize=7, leading=9)
_body_style = _styles["BodyText"]
_h1_style = _styles["Heading1"]
_h2_style = _styles["Heading2"]

_HEADER_ROW_STYLE = [
    ("FONTSIZE", (0, 0), (-1, -1), 7),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
]


def _fmt_ts(ms) -> str:
    if ms is None:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_num(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _cell(text: str) -> Paragraph:
    return Paragraph(text, _cell_style)


def _format_event(event: dict) -> str:
    if event["type"] == "drug":
        return f"Drug: {event['drugName']} {_fmt_num(event['dose'])}{event['unit']}"
    if event["type"] == "note":
        return f"Note: {event['text']}"
    if event["type"] == "alert":
        return f"Alert: {event['message']}"
    return str(event)


def _combined_corrections(db: OrmSession, session_id: str) -> List[dict]:
    """Merges S8's vital-reading audit trail and S9's per-drug correction
    history into one chronologically-sorted list for the PDF's correction
    section — the two live in separate tables/shapes (see repo.py /
    DrugEventRow docstrings for why), but a reviewer reading the PDF needs
    one timeline, not two."""
    entries: List[dict] = []

    for a in repo.list_audit(db, session_id):
        if a.action == "human_correct":
            description = f"{a.author} corrected {a.vital.upper()} from {a.prev_value} to {a.value}"
        else:
            description = f"{a.author} dismissed AI reading {a.vital.upper()} = {a.value}"
        entries.append({"timestamp": a.timestamp, "source": "Vitals audit", "description": description})

    for d in repo.list_drug_events(db, session_id):
        for c in repo.list_drug_corrections(db, d.id):
            description = f"{c['author']} corrected {d.drug_name} {c['field']} from {c['prevValue']} to {c['newValue']}"
            entries.append({"timestamp": c["timestamp"], "source": "Drug correction", "description": description})

    entries.sort(key=lambda e: e["timestamp"])
    return entries


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.setFillColor(colors.grey)
    canvas.drawCentredString(doc.pagesize[0] / 2, 15, DISCLAIMER)
    canvas.drawRightString(doc.pagesize[0] - 15 * mm, 15, f"Page {doc.page}")
    canvas.restoreState()


def generate_pdf(db: OrmSession, session_id: str) -> bytes:
    session = repo.get_session(db, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    chart = build_chart(db, session_id)
    drug_events = repo.list_drug_events(db, session_id)
    corrections = _combined_corrections(db, session_id)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=15 * mm, rightMargin=15 * mm
    )
    story = []

    story.append(Paragraph("Anaesthesia Record", _h1_style))
    story.append(Paragraph(session.procedure, _h2_style))
    story.append(Spacer(1, 4 * mm))

    header_rows = [
        ["Patient ID", session.patient.id, "ASA", str(session.patient.asa) if session.patient.asa else "—"],
        ["Age", _fmt_num(session.patient.age, 0), "Weight (kg)", _fmt_num(session.patient.weight, 1)],
        ["Anaesthetist", session.anesthetist, "Status", session.status],
        ["Start", _fmt_ts(session.start_time), "End", _fmt_ts(session.end_time)],
    ]
    header_table = Table(header_rows, colWidths=[35 * mm, 55 * mm, 35 * mm, 55 * mm])
    header_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 6 * mm))

    vs = chart["vitalSummary"]
    story.append(
        Paragraph(
            f"Summary — avg HR {_fmt_num(vs['avgHr'])}, min SpO2 {_fmt_num(vs['minSpo2'])}, "
            f"avg EtCO2 {_fmt_num(vs['avgEtco2'])}, duration {_fmt_num(vs['durationMin'])} min",
            _body_style,
        )
    )
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Periodic Vitals Chart", _h2_style))
    vitals_rows = [["Time", "HR", "SpO2", "NIBP", "EtCO2", "Temp", "RR", "Events"]]
    for row in chart["rows"]:
        nibp = "—"
        if row["nibpSystolic"] is not None and row["nibpDiastolic"] is not None:
            nibp = f"{_fmt_num(row['nibpSystolic'], 0)}/{_fmt_num(row['nibpDiastolic'], 0)}"
        events_text = "; ".join(_format_event(e) for e in row["events"])
        vitals_rows.append(
            [
                _fmt_ts(row["timestamp"]),
                _fmt_num(row["hr"], 0),
                _fmt_num(row["spo2"], 0),
                nibp,
                _fmt_num(row["etco2"], 0),
                _fmt_num(row["temp"], 1),
                _fmt_num(row["rr"], 0),
                _cell(events_text),
            ]
        )
    vitals_table = Table(
        vitals_rows, colWidths=[32 * mm, 12 * mm, 12 * mm, 16 * mm, 14 * mm, 12 * mm, 10 * mm, 62 * mm], repeatRows=1
    )
    vitals_table.setStyle(TableStyle(_HEADER_ROW_STYLE))
    story.append(vitals_table)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Drug Log", _h2_style))
    if drug_events:
        drug_rows = [["Time", "Drug", "Dose", "Route", "Cumulative", "Entered By"]]
        for d in drug_events:
            drug_rows.append(
                [
                    _fmt_ts(d.administered_at),
                    d.drug_name + (" (reversal)" if d.is_reversal else ""),
                    f"{_fmt_num(d.dose)} {d.unit}",
                    d.route,
                    f"{_fmt_num(d.cumulative_dose)} {d.unit}" if d.cumulative_dose is not None else "—",
                    d.entered_by,
                ]
            )
        drug_table = Table(drug_rows, colWidths=[32 * mm, 35 * mm, 25 * mm, 20 * mm, 28 * mm, 30 * mm], repeatRows=1)
        drug_table.setStyle(TableStyle(_HEADER_ROW_STYLE))
        story.append(drug_table)
    else:
        story.append(Paragraph("No drugs administered.", _body_style))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Correction History (Complete Audit Trail)", _h2_style))
    if corrections:
        corr_rows = [["Time", "Source", "Description"]]
        for c in corrections:
            corr_rows.append([_fmt_ts(c["timestamp"]), c["source"], _cell(c["description"])])
        corr_table = Table(corr_rows, colWidths=[32 * mm, 28 * mm, 110 * mm], repeatRows=1)
        corr_table.setStyle(TableStyle(_HEADER_ROW_STYLE))
        story.append(corr_table)
    else:
        story.append(Paragraph("No corrections recorded — record accepted as originally captured.", _body_style))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("Signature", _h2_style))
    sig_rows = [
        ["Signed By", session.signed_by or "—"],
        ["Method", session.signature_method or "—"],
        ["Signed At", _fmt_ts(session.signed_at)],
    ]
    sig_table = Table(sig_rows, colWidths=[35 * mm, 100 * mm])
    sig_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ]
        )
    )
    story.append(sig_table)

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
