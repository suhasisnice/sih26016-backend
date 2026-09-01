"""Objections: list, file, respond.

Legally the most consequential thing in the system after the case itself.
An objection that is never answered can invalidate the acquisition, which
is why filing is open to the affected person themselves and why every
response is recorded with who wrote it.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.ai_layer.constants import OBJECTION_RESPONSE_DAYS
from app.core.enums import AlertSeverity, ObjectionStatus, Role
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.models import Case, Objection, Person, User
from app.schemas.objection import ObjectionCreate, ObjectionList, ObjectionOut, ObjectionRespond
from app.services import audit, notify

router = APIRouter(prefix="/objections", tags=["objections"])

OBJECTION_RESPONDERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)
OPEN_STATUSES = (ObjectionStatus.FILED, ObjectionStatus.UNDER_REVIEW)


def _case_or_404(db: Session, user: User, case_id: int) -> Case:
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


def _to_out(objection: Objection, case_number: str, person_name: str, today: date) -> ObjectionOut:
    is_open = objection.status in OPEN_STATUSES
    days_open = (today - objection.filed_on).days if is_open else None
    return ObjectionOut(
        id=objection.id,
        case_id=objection.case_id,
        case_number=case_number,
        person_id=objection.person_id,
        person_name=person_name,
        grounds=objection.grounds,
        status=objection.status,
        filed_on=objection.filed_on,
        response=objection.response,
        responded_on=objection.responded_on,
        days_open=days_open,
        # Same threshold the objection_unanswered rule uses, so the case
        # page and the dashboard alert can never disagree about which
        # objections are late.
        is_overdue=bool(is_open and days_open is not None and days_open > OBJECTION_RESPONSE_DAYS),
    )


@router.get("", response_model=ObjectionList)
def list_objections(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    case_id: int | None = Query(default=None, description="Restrict to one case"),
    objection_status: ObjectionStatus | None = None,
    overdue_only: bool = False,
):
    query = (
        db.query(Objection, Case.case_number, Person.name)
        .join(Case, Objection.case_id == Case.id)
        .join(Person, Objection.person_id == Person.id)
    )
    # Scoped through the case, so a landowner sees objections on their own
    # cases and an officer only their district's.
    query = scope_cases_to_user(query, user)

    if case_id is not None:
        _case_or_404(db, user, case_id)
        query = query.filter(Objection.case_id == case_id)
    if objection_status is not None:
        query = query.filter(Objection.status == objection_status)

    today = date.today()
    items = [
        _to_out(objection, case_number, person_name, today)
        for objection, case_number, person_name in query.order_by(
            Objection.filed_on.asc(), Objection.id.asc()
        ).all()
    ]
    if overdue_only:
        items = [item for item in items if item.is_overdue]

    return ObjectionList(
        items=items,
        total=len(items),
        open_count=sum(1 for i in items if i.status in OPEN_STATUSES),
        overdue_count=sum(1 for i in items if i.is_overdue),
    )


@router.post("", response_model=ObjectionOut, status_code=status.HTTP_201_CREATED)
def file_objection(
    payload: ObjectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """File an objection against a case.

    A landowner files as themselves — the person is taken from their own
    account, never from the request body, so nobody can file in someone
    else's name. Officers recording an objection brought in person must
    name the person explicitly.
    """
    case = _case_or_404(db, user, payload.case_id)

    if user.role is Role.LANDOWNER:
        if user.person_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account is not linked to a person record",
            )
        person_id = user.person_id
    else:
        if payload.on_behalf_of_person_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="on_behalf_of_person_id is required when an officer files an objection",
            )
        if db.get(Person, payload.on_behalf_of_person_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown person"
            )
        person_id = payload.on_behalf_of_person_id

    objection = Objection(
        case_id=case.id,
        person_id=person_id,
        grounds=payload.grounds,
        status=ObjectionStatus.FILED,
        filed_on=date.today(),
    )
    db.add(objection)
    db.flush()
    audit.record(
        db,
        user,
        action="objection.file",
        entity_type="objection",
        entity_id=objection.id,
        detail=f"case {case.id}",
    )
    db.commit()

    person = db.get(Person, person_id)
    return _to_out(objection, case.case_number, person.name, date.today())


@router.post("/{objection_id}/respond", response_model=ObjectionOut)
def respond_to_objection(
    objection_id: int,
    payload: ObjectionRespond,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*OBJECTION_RESPONDERS)),
):
    """Record the decision on an objection, and the reasoning behind it."""
    objection = db.get(Objection, objection_id)
    if objection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Objection not found")
    case = _case_or_404(db, user, objection.case_id)

    if payload.status not in (
        ObjectionStatus.RESOLVED,
        ObjectionStatus.REJECTED,
        ObjectionStatus.UNDER_REVIEW,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A response must set the status to under_review, resolved or rejected",
        )

    objection.status = payload.status
    objection.response = payload.response
    # under_review is not a decision, so the clock keeps running: only a
    # resolved or rejected objection counts as answered.
    objection.responded_on = (
        date.today() if payload.status is not ObjectionStatus.UNDER_REVIEW else None
    )

    audit.record(
        db,
        user,
        action="objection.respond",
        entity_type="objection",
        entity_id=objection.id,
        detail=f"-> {payload.status.value}",
    )

    # under_review is a status change, not an answer — the objector's own
    # clock is still running, so there is nothing decided to tell them yet.
    if payload.status is not ObjectionStatus.UNDER_REVIEW:
        notify.notify_objection_filer(
            db,
            objection,
            case,
            title=f"Your objection was {payload.status.value}",
            body=f"{case.case_number}: {payload.response}",
            severity=AlertSeverity.MEDIUM,
        )

    db.commit()

    person = db.get(Person, objection.person_id)
    return _to_out(objection, case.case_number, person.name, date.today())
