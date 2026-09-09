"""Grievances: file, list, view, respond.

A landowner's complaint about their own case — compensation, a document
problem, a survey mismatch, a processing delay, an R&R issue. Deliberately
separate from Objection (app.routers.objections): that is the Act's own
Sec. 15 step; this is everything else. See app.models.tables.Grievance's
docstring for the fuller reasoning.

Every read and write here is scoped through scope_cases_to_user, the same
single gate every other case-related router uses — a landowner cannot list,
view, or respond to a grievance on a case that is not theirs, and cannot
manufacture one against another person's case by sending a case_id they are
not entitled to: _case_or_404 checks the case's entitlement before anything
about the grievance is read or written, exactly like objections.py does.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import (
    AlertSeverity,
    GrievanceCategory,
    GrievanceContactMethod,
    GrievanceStatus,
    Role,
)
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.models import Case, Grievance, GrievanceStatusHistory, Person, User
from app.routers.documents import ALLOWED_CONTENT_TYPES
from app.schemas.grievance import (
    GrievanceDetail,
    GrievanceList,
    GrievanceOut,
    GrievanceRespond,
    GrievanceStatusHistoryOut,
)
from app.services import audit, notify, numbering
from app.services.grievances import GRIEVANCE_CATEGORY_ROLE, GRIEVANCE_RESPONDERS, OPEN_STATUSES, record_status_change
from app.services.uploads import save_upload_file

router = APIRouter(prefix="/grievances", tags=["grievances"])


def _case_or_404(db: Session, user: User, case_id: int) -> Case:
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


def _visible_grievance_or_404(db: Session, user: User, grievance_id: int) -> tuple[Grievance, Case]:
    """404, not 403, for a grievance outside the user's scope — same reason
    _get_visible_case gives: a 403 would confirm the grievance exists,
    which a landowner should not be able to fish for by trying ids."""
    grievance = db.get(Grievance, grievance_id)
    if grievance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grievance not found")
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == grievance.case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grievance not found")
    return grievance, case


def _to_out(grievance: Grievance, case_number: str, person_name: str, today: date) -> GrievanceOut:
    is_open = grievance.status in OPEN_STATUSES
    days_open = (today - grievance.filed_on).days if is_open else None
    return GrievanceOut(
        id=grievance.id,
        grievance_number=grievance.grievance_number,
        case_id=grievance.case_id,
        case_number=case_number,
        person_id=grievance.person_id,
        person_name=person_name,
        category=grievance.category,
        assigned_role=GRIEVANCE_CATEGORY_ROLE.get(grievance.category, Role.SLAO),
        subject=grievance.subject,
        description=grievance.description,
        status=grievance.status,
        preferred_contact_method=grievance.preferred_contact_method,
        filed_on=grievance.filed_on,
        response=grievance.response,
        responded_on=grievance.responded_on,
        has_attachment=grievance.attachment_stored_name is not None,
        attachment_filename=grievance.attachment_filename,
        days_open=days_open,
    )


@router.get("", response_model=GrievanceList)
def list_grievances(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    case_id: int | None = Query(default=None, description="Restrict to one case"),
    grievance_status: GrievanceStatus | None = None,
):
    """Grievances for the cases this user may see — a landowner sees only
    their own, an officer only their district's, exactly the same scoping
    every other case-related list in this API uses."""
    query = (
        db.query(Grievance, Case.case_number, Person.name)
        .join(Case, Grievance.case_id == Case.id)
        .join(Person, Grievance.person_id == Person.id)
    )
    query = scope_cases_to_user(query, user)

    if case_id is not None:
        _case_or_404(db, user, case_id)
        query = query.filter(Grievance.case_id == case_id)
    if grievance_status is not None:
        query = query.filter(Grievance.status == grievance_status)

    today = date.today()
    items = [
        _to_out(grievance, case_number, person_name, today)
        for grievance, case_number, person_name in query.order_by(
            Grievance.filed_on.desc(), Grievance.id.desc()
        ).all()
    ]

    return GrievanceList(
        items=items,
        total=len(items),
        open_count=sum(1 for i in items if i.status in OPEN_STATUSES),
    )


@router.get("/{grievance_id}", response_model=GrievanceDetail)
def get_grievance(
    grievance_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    grievance, case = _visible_grievance_or_404(db, user, grievance_id)
    person = db.get(Person, grievance.person_id)

    history_rows = (
        db.query(GrievanceStatusHistory, User.full_name)
        .outerjoin(User, GrievanceStatusHistory.changed_by_user_id == User.id)
        .filter(GrievanceStatusHistory.grievance_id == grievance.id)
        .order_by(GrievanceStatusHistory.changed_on.asc(), GrievanceStatusHistory.id.asc())
        .all()
    )
    history = [
        GrievanceStatusHistoryOut(
            from_status=row.from_status,
            to_status=row.to_status,
            changed_on=row.changed_on,
            changed_by_name=changed_by_name,
            note=row.note,
        )
        for row, changed_by_name in history_rows
    ]

    base = _to_out(grievance, case.case_number, person.name, date.today())
    return GrievanceDetail(**base.model_dump(), history=history)


@router.get("/{grievance_id}/attachment")
def download_grievance_attachment(
    grievance_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Stream a grievance's supporting attachment back, after checking the
    caller may see the grievance it belongs to — same entitlement check as
    the grievance itself, and the same path-safety guard
    documents.download_document uses."""
    grievance, _case = _visible_grievance_or_404(db, user, grievance_id)
    if grievance.attachment_stored_name is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No attachment on this grievance")

    upload_dir = Path(settings.upload_dir).resolve()
    path = (upload_dir / grievance.attachment_stored_name).resolve()
    if path.parent != upload_dir or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment file is not on disk")

    audit.record(
        db,
        user,
        action="grievance.download_attachment",
        entity_type="grievance",
        entity_id=grievance.id,
        detail=grievance.grievance_number,
    )
    db.commit()

    return FileResponse(
        path, media_type=grievance.attachment_content_type, filename=grievance.attachment_filename
    )


@router.post("", response_model=GrievanceDetail, status_code=status.HTTP_201_CREATED)
async def file_grievance(
    case_id: int = Form(...),
    category: GrievanceCategory = Form(...),
    subject: str = Form(..., min_length=3, max_length=200),
    description: str = Form(..., min_length=10, max_length=2000),
    preferred_contact_method: GrievanceContactMethod = Form(default=GrievanceContactMethod.SMS),
    on_behalf_of_person_id: int | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Raise a grievance against a case.

    A landowner files as themselves — the person comes from their own
    account, never from the request — so nobody can raise a grievance
    against a case they do not own by sending someone else's case_id: the
    case_id is checked against this user's own entitlement first
    (_case_or_404, via scope_cases_to_user), and only THEN is a grievance
    created against it. An officer recording one on a citizen's behalf must
    name the person explicitly, same as objections.py's file_objection.
    """
    case = _case_or_404(db, user, case_id)

    if user.role is Role.LANDOWNER:
        if user.person_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account is not linked to a person record",
            )
        person_id = user.person_id
    else:
        if on_behalf_of_person_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="on_behalf_of_person_id is required when an officer files a grievance",
            )
        if db.get(Person, on_behalf_of_person_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown person")
        person_id = on_behalf_of_person_id

    today = date.today()
    grievance = Grievance(
        grievance_number=numbering.next_grievance_number(db, today.year),
        case_id=case.id,
        person_id=person_id,
        filed_by_user_id=user.id,
        category=category,
        subject=subject,
        description=description,
        status=GrievanceStatus.SUBMITTED,
        preferred_contact_method=preferred_contact_method,
        filed_on=today,
    )

    if file is not None:
        saved = await save_upload_file(file, ALLOWED_CONTENT_TYPES)
        grievance.attachment_stored_name = saved.stored_name
        grievance.attachment_filename = Path(file.filename or "attachment").name[:255]
        grievance.attachment_content_type = file.content_type
        grievance.attachment_size_bytes = saved.size_bytes
        grievance.attachment_sha256 = saved.sha256_hex

    db.add(grievance)
    db.flush()

    db.add(
        GrievanceStatusHistory(
            grievance_id=grievance.id,
            from_status=None,
            to_status=GrievanceStatus.SUBMITTED,
            changed_by_user_id=user.id,
            changed_on=today,
            note=None,
        )
    )

    audit.record(
        db,
        user,
        action="grievance.file",
        entity_type="grievance",
        entity_id=grievance.id,
        detail=f"case {case.id} category={category.value}",
    )
    db.commit()
    db.refresh(grievance)

    person = db.get(Person, person_id)
    base = _to_out(grievance, case.case_number, person.name, today)
    return GrievanceDetail(
        **base.model_dump(),
        history=[
            GrievanceStatusHistoryOut(
                from_status=None,
                to_status=GrievanceStatus.SUBMITTED,
                changed_on=today,
                changed_by_name=user.full_name,
                note=None,
            )
        ],
    )


@router.post("/{grievance_id}/respond", response_model=GrievanceDetail)
def respond_to_grievance(
    grievance_id: int,
    payload: GrievanceRespond,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*GRIEVANCE_RESPONDERS)),
):
    """Move a grievance along — assign it, request more information,
    answer it, or close it — recording who did it and why."""
    grievance, case = _visible_grievance_or_404(db, user, grievance_id)

    if grievance.status in (GrievanceStatus.RESOLVED, GrievanceStatus.CLOSED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This grievance is already {grievance.status.value} and cannot be updated further",
        )

    today = date.today()
    record_status_change(db, grievance, payload.status, user, note=payload.response, on_date=today)
    grievance.response = payload.response
    # A status change that is not itself the final word (assigned, under
    # review, info required) is not "answered" — the same distinction
    # objections.py draws for under_review, so the filer's own clock and
    # the "responded_on" figure only move on an actual answer.
    if payload.status in (GrievanceStatus.RESPONSE_PROVIDED, GrievanceStatus.RESOLVED, GrievanceStatus.CLOSED):
        grievance.responded_on = today
        grievance.responded_by_user_id = user.id

    audit.record(
        db,
        user,
        action="grievance.respond",
        entity_type="grievance",
        entity_id=grievance.id,
        detail=f"-> {payload.status.value}",
    )

    notify.notify_grievance_filer(
        db,
        grievance,
        case,
        title=f"Your grievance {grievance.grievance_number} was updated",
        body=f"{grievance.grievance_number}: now {payload.status.value.replace('_', ' ')} — {payload.response}",
        severity=AlertSeverity.MEDIUM,
    )

    db.commit()
    db.refresh(grievance)
    return get_grievance(grievance.id, db=db, user=user)
