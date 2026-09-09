"""The per-case PDF status report — GET /cases/{id}/report.pdf.

Every figure here is read the same way the case detail screen and the MIS
exports already read it (see app.routers.cases's _parcel_totals,
_consent_progress, and app.services.sla/statutes) — this file assembles
the same facts into a printable document, it does not compute anything
new. The one field with no real data behind it is "Responsible Officer":
no table in this schema names a specific officer as owning a case (the
closest things are SurveyTask.assigned_to_user_id, which is one field
job, not case ownership, and CaseStageHistory.changed_by_user_id, which
is a historical fact about who last moved it). _RESPONSIBLE_ROLE names
the *role* RFCTLARR/this system's own workflow makes accountable for a
given stage — an SLAO, a District Collector — not a person, the same way
a citizen reading a real government notice sees "Special Land Acquisition
Officer" on it, not a name.
"""

from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import Stage
from app.models import Case, CaseStageHistory, Document, Parcel, RequiredDocument, Statute, StatuteStageReference
from app.services import sla, workflow
from app.services.statutes import RFCTLARR, STATUTES

INK = colors.HexColor("#1F2A24")
INK_SOFT = colors.HexColor("#4A5247")
LINE = colors.HexColor("#B8AE93")
HEADER_BG = colors.HexColor("#EFEAD9")

_RESPONSIBLE_ROLE = {
    Stage.PRELIMINARY_NOTIFICATION: "Special Land Acquisition Officer",
    Stage.SOCIAL_IMPACT_ASSESSMENT: "Special Land Acquisition Officer",
    Stage.LAND_VERIFICATION: "Field Officer",
    Stage.OBJECTION_PERIOD: "Special Land Acquisition Officer",
    Stage.DECLARATION: "Special Land Acquisition Officer",
    Stage.AWARD: "Special Land Acquisition Officer",
    Stage.REHABILITATION_RESETTLEMENT: "Rehabilitation & Resettlement Officer",
    Stage.POSSESSION: "Special Land Acquisition Officer",
    Stage.MONITORING: "District Collector",
}

_STAGE_LABEL = {
    Stage.PRELIMINARY_NOTIFICATION: "Preliminary Notification",
    Stage.SOCIAL_IMPACT_ASSESSMENT: "Social Impact Assessment",
    Stage.LAND_VERIFICATION: "Land Verification",
    Stage.OBJECTION_PERIOD: "Objection Period",
    Stage.DECLARATION: "Declaration",
    Stage.AWARD: "Award",
    Stage.REHABILITATION_RESETTLEMENT: "Rehabilitation & Resettlement",
    Stage.POSSESSION: "Possession",
    Stage.MONITORING: "Monitoring",
}

styles = {
    "eyebrow": ParagraphStyle("eyebrow", fontName="Helvetica", fontSize=9, leading=12, textColor=INK_SOFT, alignment=1),
    "brand": ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=INK, alignment=1),
    "reportTitle": ParagraphStyle("reportTitle", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=INK, alignment=1, spaceBefore=4),
    "sectionHead": ParagraphStyle("sectionHead", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK, spaceBefore=16, spaceAfter=8),
    "label": ParagraphStyle("label", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK_SOFT),
    "value": ParagraphStyle("value", fontName="Helvetica-Bold", fontSize=9.5, leading=13, textColor=INK),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK),
    "cellHead": ParagraphStyle("cellHead", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=INK),
    "footer": ParagraphStyle("footer", fontName="Helvetica-Oblique", fontSize=8, leading=11, textColor=INK_SOFT, alignment=1),
}


def _fact_table(pairs: list[tuple[str, str]]) -> Table:
    rows = [[Paragraph(label, styles["label"]), Paragraph(str(value), styles["value"])] for label, value in pairs]
    t = Table(rows, colWidths=[1.7 * inch, 4.6 * inch])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def _statute_references(db: Session, case: Case) -> dict[Stage, StatuteStageReference]:
    statute_code = case.project.statute.code if case.project.statute_id else RFCTLARR
    rows = (
        db.query(StatuteStageReference)
        .join(Statute, StatuteStageReference.statute_id == Statute.id)
        .filter(Statute.code == statute_code)
        .all()
    )
    return {row.stage: row for row in rows}, statute_code


def _missing_documents(db: Session, case: Case) -> list[str]:
    required = {
        doc_type.value
        for (doc_type,) in db.query(RequiredDocument.doc_type).filter(RequiredDocument.stage == case.stage).all()
    }
    on_file = {
        doc_type.value
        for (doc_type,) in db.query(Document.doc_type)
        .filter(Document.case_id == case.id, Document.is_current.is_(True))
        .all()
    }
    return sorted(required - on_file)


def build_case_report_pdf(db: Session, case: Case) -> bytes:
    today = date.today()

    # Kept simple and local to this report rather than importing the
    # routers module's private _parcel_totals — one grouped query for one
    # case is not worth a cross-module import.
    parcel_count, total_area = (
        db.query(func.count(Parcel.id), func.coalesce(func.sum(Parcel.area_ha), 0.0))
        .filter(Parcel.case_id == case.id)
        .one()
    )
    total_area = round(float(total_area), 2)

    stage_history = (
        db.query(CaseStageHistory)
        .filter(CaseStageHistory.case_id == case.id)
        .order_by(CaseStageHistory.changed_on.asc(), CaseStageHistory.id.asc())
        .all()
    )
    reached_on: dict[Stage, date] = {h.to_stage: h.changed_on for h in stage_history}

    refs, statute_code = _statute_references(db, case)
    statute_name = STATUTES.get(statute_code, statute_code)

    sla_table = sla.load_sla(db)
    timeline_status = sla.timeline_status(case.stage_due_on, case.stage, today, sla_table)
    days_remaining = sla.days_remaining(case.stage_due_on, today)

    missing_docs = _missing_documents(db, case)
    if missing_docs:
        next_action = f"Upload {len(missing_docs)} required document(s)"
    elif case.stage is workflow.TERMINAL_STAGE:
        next_action = "None — case has reached its final stage"
    else:
        next_action = f"Advance case to {_STAGE_LABEL[workflow.next_stage(case.stage)]}"

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        title=f"Case Status Report — {case.case_number}",
        author="BhoomiMitra",
    )

    story = []

    # ---- header ----
    story.append(Paragraph("BHOOMIMITRA", styles["brand"]))
    story.append(Paragraph("LAND ACQUISITION CASE SYSTEM", styles["eyebrow"]))
    story.append(Paragraph("CASE STATUS REPORT", styles["reportTitle"]))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1, color=LINE))
    story.append(Spacer(1, 8))

    story.append(
        _fact_table(
            [
                ("Case Reference", case.case_number),
                ("Project", case.project.name),
                ("Location", f"{case.village.name}, {case.district.name}"),
                ("Current Stage", _STAGE_LABEL[case.stage]),
                ("Status", case.status.value.replace("_", " ").title()),
                ("Date Generated", today.strftime("%d %B %Y")),
            ]
        )
    )
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=LINE))

    # ---- 1. case summary ----
    story.append(Paragraph("1.&nbsp; CASE SUMMARY", styles["sectionHead"]))
    story.append(
        _fact_table(
            [
                ("Requiring Body", case.project.requiring_body),
                ("District / State", f"{case.district.name}, {case.district.state.name}"),
                ("Governing Statute", statute_name),
                ("Case Opened On", case.created_at.strftime("%d %B %Y")),
            ]
        )
    )

    # ---- 2. acquisition details ----
    story.append(Paragraph("2.&nbsp; ACQUISITION DETAILS", styles["sectionHead"]))
    story.append(
        _fact_table(
            [
                ("Parcels", str(parcel_count)),
                ("Total Area", f"{total_area} ha"),
                ("Village", case.village.name),
            ]
        )
    )

    # ---- 3. statutory / procedural progress ----
    story.append(Paragraph("3.&nbsp; STATUTORY / PROCEDURAL PROGRESS", styles["sectionHead"]))
    header_row = [Paragraph(h, styles["cellHead"]) for h in ("Stage", "Provision", "Status", "Date")]
    table_rows = [header_row]
    for stage in workflow.STAGE_ORDER:
        ref = refs.get(stage)
        if ref is None:
            provision = "—"
        elif ref.is_applicable:
            provision = ref.section_reference or "—"
        else:
            provision = ref.note or "Not applicable"

        when = reached_on.get(stage)
        if stage == case.stage:
            row_status = {
                "on_time": "In progress — on time",
                "at_risk": "In progress — at risk",
                "breached": "In progress — overdue",
            }.get(timeline_status.value if hasattr(timeline_status, "value") else timeline_status, "In progress")
            when_text = when.strftime("%d %b %Y") if when else "—"
        elif when is not None:
            row_status = "Completed"
            when_text = when.strftime("%d %b %Y")
        else:
            row_status = "Pending"
            when_text = "—"

        table_rows.append(
            [
                Paragraph(_STAGE_LABEL[stage], styles["cell"]),
                Paragraph(provision, styles["cell"]),
                Paragraph(row_status, styles["cell"]),
                Paragraph(when_text, styles["cell"]),
            ]
        )

    progress_table = Table(table_rows, colWidths=[1.55 * inch, 1.55 * inch, 1.55 * inch, 1.0 * inch], repeatRows=1)
    progress_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, LINE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(progress_table)

    # ---- 4. current action ----
    story.append(Paragraph("4.&nbsp; CURRENT ACTION", styles["sectionHead"]))
    due_text = case.stage_due_on.strftime("%d %B %Y") if case.stage_due_on else "Not set"
    if days_remaining is not None:
        due_text += f"  ({days_remaining} day(s) remaining)" if days_remaining >= 0 else f"  ({-days_remaining} day(s) overdue)"
    story.append(
        KeepTogether(
            [
                _fact_table(
                    [
                        ("Responsible Officer", _RESPONSIBLE_ROLE.get(case.stage, "District Collector")),
                        ("Next Action", next_action),
                        ("Due Date", due_text),
                    ]
                )
            ]
        )
    )

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=1, color=LINE))
    story.append(Spacer(1, 6))
    story.append(Paragraph("System-generated case status report. For project/prototype use.", styles["footer"]))

    def _page_number(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(INK_SOFT)
        canvas.drawCentredString(LETTER[0] / 2, 0.4 * inch, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    return buffer.getvalue()
