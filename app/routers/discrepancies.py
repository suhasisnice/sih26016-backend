"""Field-survey discrepancy reports: file, list, respond.

Mirrors app.routers.objections deliberately — same
list/create/respond shape — but raised by the field officer doing a survey
rather than the affected person, and scoped to the survey task the mismatch
was found on. See SurveyDiscrepancy's docstring in app.models.tables for why
this is its own entity rather than reusing Objection.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity, DiscrepancyStatus, Role
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.models import Case, SurveyDiscrepancy, SurveyTask, User
from app.schemas.discrepancy import (
    DiscrepancyCreate,
    DiscrepancyList,
    DiscrepancyOut,
    DiscrepancyRespond,
)
from app.services import audit, notify

router = APIRouter(prefix="/discrepancies", tags=["discrepancies"])

# Whoever can work a field survey can flag one — same set as
# survey.SURVEY_PERFORMERS.
DISCREPANCY_FILERS = (Role.ADMIN, Role.FIELD_OFFICER)
# Whoever reviews a survey also clears the discrepancies raised on it — same
# set as survey.SURVEY_REVIEWERS.
DISCREPANCY_REVIEWERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)


def _task_or_404(db: Session, user: User, task_id: int) -> SurveyTask:
    task = db.get(SurveyTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Survey task not found")
    visible_case = scope_cases_to_user(db.query(Case), user).filter(Case.id == task.case_id).first()
    if visible_case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Survey task not found")
    return task


def _to_out(db: Session, row: SurveyDiscrepancy) -> DiscrepancyOut:
    case = db.get(Case, row.case_id)
    filer = db.get(User, row.filed_by_user_id)
    responder = db.get(User, row.responded_by_user_id) if row.responded_by_user_id else None
    return DiscrepancyOut(
        id=row.id,
        survey_task_id=row.survey_task_id,
        case_id=row.case_id,
        case_number=case.case_number,
        discrepancy_type=row.discrepancy_type,
        description=row.description,
        status=row.status,
        filed_by_name=filer.full_name if filer else "Unknown",
        filed_on=row.filed_on,
        response=row.response,
        responded_by_name=responder.full_name if responder else None,
        responded_on=row.responded_on,
    )


@router.get("", response_model=DiscrepancyList)
def list_discrepancies(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    case_id: int | None = Query(default=None),
    survey_task_id: int | None = Query(default=None),
    discrepancy_status: DiscrepancyStatus | None = None,
):
    query = (
        db.query(SurveyDiscrepancy)
        .join(Case, SurveyDiscrepancy.case_id == Case.id)
    )
    query = scope_cases_to_user(query, user)
    if case_id is not None:
        query = query.filter(SurveyDiscrepancy.case_id == case_id)
    if survey_task_id is not None:
        query = query.filter(SurveyDiscrepancy.survey_task_id == survey_task_id)
    if discrepancy_status is not None:
        query = query.filter(SurveyDiscrepancy.status == discrepancy_status)

    rows = query.order_by(SurveyDiscrepancy.filed_on.desc(), SurveyDiscrepancy.id.desc()).all()
    items = [_to_out(db, row) for row in rows]
    return DiscrepancyList(
        items=items,
        total=len(items),
        open_count=sum(1 for i in items if i.status == DiscrepancyStatus.OPEN),
    )


@router.post("", response_model=DiscrepancyOut, status_code=status.HTTP_201_CREATED)
def file_discrepancy(
    payload: DiscrepancyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*DISCREPANCY_FILERS)),
):
    """Flag a mismatch found during a field survey. Creates a review task
    for a supervising officer — it never edits the case, parcel, or person
    record it disagrees with."""
    task = _task_or_404(db, user, payload.survey_task_id)
    if user.role != Role.ADMIN and task.assigned_to_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This survey is not assigned to you")

    row = SurveyDiscrepancy(
        survey_task_id=task.id,
        case_id=task.case_id,
        discrepancy_type=payload.discrepancy_type,
        description=payload.description,
        status=DiscrepancyStatus.OPEN,
        filed_by_user_id=user.id,
        filed_on=date.today(),
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        user,
        action="discrepancy.file",
        entity_type="survey_discrepancy",
        entity_id=row.id,
        detail=f"survey_task={task.id} case={task.case_id} type={payload.discrepancy_type.value}",
    )
    db.commit()
    return _to_out(db, row)


@router.post("/{discrepancy_id}/respond", response_model=DiscrepancyOut)
def respond_to_discrepancy(
    discrepancy_id: int,
    payload: DiscrepancyRespond,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*DISCREPANCY_REVIEWERS)),
):
    row = db.get(SurveyDiscrepancy, discrepancy_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Discrepancy not found")
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == row.case_id).first()
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Discrepancy not found")
    if row.status != DiscrepancyStatus.OPEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This discrepancy is already resolved")

    row.status = DiscrepancyStatus.RESOLVED
    row.response = payload.response
    row.responded_by_user_id = user.id
    row.responded_on = date.today()

    audit.record(
        db,
        user,
        action="discrepancy.respond",
        entity_type="survey_discrepancy",
        entity_id=row.id,
        detail=payload.response,
    )
    notify.notify_user(
        db,
        user_id=row.filed_by_user_id,
        title="Discrepancy report resolved",
        body=f"{case.case_number}: {payload.response}",
        severity=AlertSeverity.LOW,
        case_id=case.id,
    )
    db.commit()
    db.refresh(row)
    return _to_out(db, row)
