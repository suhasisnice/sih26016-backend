"""MIS exports — the reports a reviewing officer actually asks for.

The dashboard answers "how are we doing". A report answers "give me the
rows so I can check". The system had no way to produce the second, which is
the first thing anyone senior asks for and the reason so much of this work
still happens in spreadsheets.

Three decisions worth stating:

- **Streamed, not assembled.** Rows are yielded through a generator, so a
  national export does not build a multi-megabyte string in memory before
  the first byte reaches the client.

- **Scoped exactly like the screens.** Every export runs through the same
  entitlement helpers as the list routes. An export that could see more than
  the page it was launched from would be a way to walk around the access
  control, and it is exactly the kind of endpoint where that gets missed.

- **Exports are audited.** Downloading the compensation register for a
  district is a more sensitive act than looking at one case, and it is the
  kind of thing an audit trail exists to record.

CSV rather than XLSX deliberately: it opens in every spreadsheet, needs no
dependency in the container, and streams. A UTF-8 BOM is written so that
Excel on Windows renders Kannada and Devanagari place names correctly
instead of as mojibake — without it, the first thing a reviewer sees is a
column of broken characters.
"""

import csv
import io
from datetime import date
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.ai_layer.kpis import compute_kpis
from app.core.enums import CaseStatus, Role, Stage
from app.dependencies import entitled_case_ids, get_current_user, get_db, scope_cases_to_user
from app.models import (
    AffectedFamily,
    Case,
    Compensation,
    District,
    Parcel,
    Person,
    Project,
    RnRRecord,
    State,
    User,
    Village,
)
from app.services import audit, sla

router = APIRouter(prefix="/exports", tags=["exports"])

# Excel on Windows assumes the system codepage unless a BOM says otherwise.
BOM = "﻿"

# A hard ceiling so one request cannot stream the entire national dataset.
# Generous enough for a real district or state export; the message names the
# limit rather than silently truncating, because a report that quietly stops
# short is worse than one that refuses.
MAX_EXPORT_ROWS = 50_000


def _writer() -> tuple[io.StringIO, "csv.writer"]:
    buffer = io.StringIO()
    return buffer, csv.writer(buffer, lineterminator="\n")


def _flush(buffer: io.StringIO) -> str:
    value = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return value


def _stream(header: list[str], rows: Iterator[list]) -> Iterator[str]:
    """Yield a CSV a row at a time, starting with the BOM and header."""
    buffer, writer = _writer()
    yield BOM
    writer.writerow(header)
    yield _flush(buffer)
    for row in rows:
        writer.writerow(row)
        yield _flush(buffer)


def _csv_response(filename: str, header: list[str], rows: Iterator[list]) -> StreamingResponse:
    return StreamingResponse(
        _stream(header, rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _scoped_cases(db: Session, user: User, state_id: int | None, district_id: int | None,
                  project_id: int | None, stage: Stage | None, case_status: CaseStatus | None):
    """One scoping path for every export, so none of them can drift."""
    query = (
        db.query(Case, District.name, Village.name, Project.name, State.name)
        .join(District, Case.district_id == District.id)
        .join(State, District.state_id == State.id)
        .join(Village, Case.village_id == Village.id)
        .join(Project, Case.project_id == Project.id)
    )
    query = scope_cases_to_user(query, user)

    if state_id is not None:
        query = query.filter(District.state_id == state_id)
    if district_id is not None:
        query = query.filter(Case.district_id == district_id)
    if project_id is not None:
        query = query.filter(Case.project_id == project_id)
    if stage is not None:
        query = query.filter(Case.stage == stage)
    if case_status is not None:
        query = query.filter(Case.status == case_status)
    return query.order_by(Case.case_number)


@router.get("/cases.csv")
def export_cases(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    state_id: int | None = None,
    district_id: int | None = None,
    project_id: int | None = None,
    stage: Stage | None = None,
    case_status: CaseStatus | None = None,
):
    """The case register, with timeline position on every row."""
    query = _scoped_cases(db, user, state_id, district_id, project_id, stage, case_status)
    rows = query.limit(MAX_EXPORT_ROWS + 1).all()
    if len(rows) > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"That selection exceeds the {MAX_EXPORT_ROWS:,}-row export limit. "
                f"Narrow it by state, district or stage."
            ),
        )

    today = date.today()
    sla_table = sla.load_sla(db)

    # Parcel totals in one grouped query rather than one per case.
    case_ids = [case.id for case, *_ in rows]
    totals = {}
    if case_ids:
        from sqlalchemy import func

        totals = {
            cid: (count, round(float(area), 4))
            for cid, count, area in db.query(
                Parcel.case_id,
                func.count(Parcel.id),
                func.coalesce(func.sum(Parcel.area_ha), 0.0),
            )
            .filter(Parcel.case_id.in_(case_ids))
            .group_by(Parcel.case_id)
            .all()
        }

    def generate():
        for case, district_name, village_name, project_name, state_name in rows:
            parcel_count, area = totals.get(case.id, (0, 0.0))
            status_value = sla.timeline_status(case.stage_due_on, case.stage, today, sla_table)
            yield [
                case.case_number,
                case.title,
                state_name,
                district_name,
                village_name,
                project_name,
                case.stage.value,
                case.status.value,
                case.created_at.isoformat(),
                case.stage_changed_at.isoformat(),
                (today - case.stage_changed_at).days,
                case.stage_due_on.isoformat() if case.stage_due_on else "",
                sla.days_remaining(case.stage_due_on, today) if case.stage_due_on else "",
                status_value.value,
                parcel_count,
                area,
            ]

    audit.record(
        db,
        user,
        action="export.cases",
        entity_type="case",
        detail=f"{len(rows)} rows (state={state_id} district={district_id} stage={stage})",
    )
    db.commit()

    return _csv_response(
        f"cases_{today.isoformat()}.csv",
        [
            "case_number", "title", "state", "district", "village", "project",
            "stage", "status", "opened_on", "stage_changed_on", "days_in_stage",
            "stage_due_on", "days_remaining", "timeline_status",
            "parcel_count", "total_area_ha",
        ],
        generate(),
    )


@router.get("/compensation.csv")
def export_compensation(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    state_id: int | None = None,
    district_id: int | None = None,
    case_id: int | None = None,
):
    """The compensation register: one row per beneficiary per case.

    Officers only. This carries names against amounts, which is the most
    sensitive combination in the system — a landowner may see their own
    entitlement on their own case page, but nobody downloads a village's
    payment list as a spreadsheet.
    """
    if user.role not in (
        Role.ADMIN,
        Role.DISTRICT_OFFICER,
        Role.SLAO,
        Role.STATE_OFFICER,
        Role.MINISTRY_OFFICER,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role.value}' may not export the compensation register",
        )

    entitled = entitled_case_ids(db, user)

    query = (
        db.query(Compensation, Case.case_number, Person.name, District.name, Village.name)
        .join(Case, Compensation.case_id == Case.id)
        .join(Person, Compensation.person_id == Person.id)
        .join(District, Case.district_id == District.id)
        .join(Village, Case.village_id == Village.id)
    )
    if entitled is not None:
        if not entitled:
            return _csv_response("compensation.csv", ["case_number"], iter(()))
        query = query.filter(Compensation.case_id.in_(entitled))
    if state_id is not None:
        query = query.filter(District.state_id == state_id)
    if district_id is not None:
        query = query.filter(Case.district_id == district_id)
    if case_id is not None:
        query = query.filter(Compensation.case_id == case_id)

    rows = query.order_by(Case.case_number, Person.name).limit(MAX_EXPORT_ROWS + 1).all()
    if len(rows) > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That selection exceeds the {MAX_EXPORT_ROWS:,}-row export limit.",
        )

    def generate():
        for comp, case_number, person_name, district_name, village_name in rows:
            yield [
                case_number,
                district_name,
                village_name,
                person_name,
                comp.amount_awarded,
                comp.amount_paid,
                comp.amount_awarded - comp.amount_paid,
                comp.status.value,
                comp.awarded_on.isoformat() if comp.awarded_on else "",
            ]

    audit.record(
        db,
        user,
        action="export.compensation",
        entity_type="compensation",
        detail=f"{len(rows)} rows (state={state_id} district={district_id} case={case_id})",
    )
    db.commit()

    return _csv_response(
        f"compensation_{date.today().isoformat()}.csv",
        [
            "case_number", "district", "village", "beneficiary",
            "amount_awarded", "amount_paid", "amount_pending",
            "status", "awarded_on",
        ],
        generate(),
    )


@router.get("/families.csv")
def export_families(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    state_id: int | None = None,
    district_id: int | None = None,
    displaced_only: bool = False,
):
    """Affected and displaced households, with their R&R status.

    The register behind the two family figures on the dashboard, so a number
    that looks wrong can be traced to the rows that produced it.
    """
    if user.role not in (
        Role.ADMIN,
        Role.DISTRICT_OFFICER,
        Role.SLAO,
        Role.RNR_OFFICER,
        Role.STATE_OFFICER,
        Role.MINISTRY_OFFICER,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role.value}' may not export the family register",
        )

    entitled = entitled_case_ids(db, user)

    query = (
        db.query(AffectedFamily, Case.case_number, Person.name, District.name, Village.name)
        .join(Case, AffectedFamily.case_id == Case.id)
        .join(Person, AffectedFamily.person_id == Person.id)
        .join(District, Case.district_id == District.id)
        .join(Village, Person.village_id == Village.id)
    )
    if entitled is not None:
        if not entitled:
            return _csv_response("families.csv", ["case_number"], iter(()))
        query = query.filter(AffectedFamily.case_id.in_(entitled))
    if state_id is not None:
        query = query.filter(District.state_id == state_id)
    if district_id is not None:
        query = query.filter(Case.district_id == district_id)
    if displaced_only:
        query = query.filter(AffectedFamily.is_displaced.is_(True))

    rows = query.order_by(Case.case_number, Person.name).limit(MAX_EXPORT_ROWS + 1).all()
    if len(rows) > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That selection exceeds the {MAX_EXPORT_ROWS:,}-row export limit.",
        )

    # R&R status per (case, person), in one lookup.
    rnr = {
        (row.case_id, row.person_id): row.status.value
        for row in db.query(RnRRecord).filter(
            RnRRecord.case_id.in_([f.case_id for f, *_ in rows] or [0])
        )
    }

    def generate():
        for family, case_number, person_name, district_name, village_name in rows:
            yield [
                case_number,
                district_name,
                village_name,
                person_name,
                "yes" if family.is_landowner else "no",
                "yes" if family.is_displaced else "no",
                rnr.get((family.case_id, family.person_id), ""),
            ]

    audit.record(
        db,
        user,
        action="export.families",
        entity_type="affected_family",
        detail=f"{len(rows)} rows (displaced_only={displaced_only})",
    )
    db.commit()

    return _csv_response(
        f"families_{date.today().isoformat()}.csv",
        ["case_number", "district", "village", "household", "is_landowner", "is_displaced", "rnr_status"],
        generate(),
    )


@router.get("/kpis.csv")
def export_kpis(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    group_by: str = Query(default="district", pattern="^(district|state|project|stage)$"),
    state_id: int | None = None,
):
    """The dashboard as a table — one row per district, state, project or stage.

    This is the "customisable MIS report" the statement asks for, in its
    honest form: the same figures the dashboard shows, grouped by whichever
    dimension the reviewer needs, rather than a report builder nobody would
    finish in time and nobody would trust.
    """
    entitled = entitled_case_ids(db, user)
    today = date.today()

    # Build the grouping set, then compute the same KPI bundle per group so
    # a row of this export and a tile on the dashboard can never disagree.
    if group_by == "state":
        groups = [
            (s.id, s.name, {"state_id": s.id})
            for s in db.query(State).order_by(State.name).all()
        ]
    elif group_by == "district":
        dq = db.query(District).order_by(District.name)
        if state_id is not None:
            dq = dq.filter(District.state_id == state_id)
        groups = [(d.id, d.name, {"district_id": d.id}) for d in dq.all()]
    elif group_by == "project":
        pq = db.query(Project).order_by(Project.name)
        groups = [(p.id, p.name, {"project_id": p.id}) for p in pq.all()]
    else:
        groups = [(s.value, s.value, {}) for s in Stage]

    header = [
        group_by, "cases", "area_notified_ha", "area_acquired_ha",
        "compensation_awarded", "compensation_paid", "compensation_pending",
        "affected_families", "displaced_families",
        "rnr_completed", "rnr_entitled",
        "possession_taken", "possession_pending",
        "notifications_issued", "awards_declared",
        "timeline_breached", "timeline_adherence_pct",
    ]

    def generate():
        for _group_id, label, filters in groups:
            if group_by == "stage":
                # Stage rows need a case-id set filtered by stage, which the
                # KPI scope resolver does not take as a filter.
                stage_cases = db.query(Case.id).filter(Case.stage == Stage(label))
                if entitled is not None:
                    stage_cases = stage_cases.filter(Case.id.in_(entitled))
                ids = [cid for (cid,) in stage_cases.all()]
                from app.ai_layer.kpis import compute_kpis as _kpis

                kpis = _kpis(db, base_case_ids=ids, as_of=today)
            else:
                try:
                    kpis = compute_kpis(db, base_case_ids=entitled, as_of=today, **filters)
                except ValueError:
                    continue

            if kpis["scope"]["case_count"] == 0:
                continue
            yield [
                label,
                kpis["scope"]["case_count"],
                kpis["area_notified_ha"],
                kpis["area_acquired_ha"],
                kpis["compensation_awarded_total"],
                kpis["compensation_paid_total"],
                kpis["compensation_pending_total"],
                kpis["affected_families_count"],
                kpis["displaced_families_count"],
                kpis["rnr_completed_count"],
                kpis["rnr_entitled_count"],
                kpis["possession_taken_count"],
                kpis["possession_pending_count"],
                kpis["notifications_issued_count"],
                kpis["awards_declared_count"],
                kpis["timeline_breached_count"],
                kpis["timeline_adherence_pct"] if kpis["timeline_adherence_pct"] is not None else "",
            ]

    audit.record(
        db, user, action="export.kpis", entity_type="dashboard", detail=f"group_by={group_by}"
    )
    db.commit()

    return _csv_response(f"mis_{group_by}_{today.isoformat()}.csv", header, generate())
