"""Field survey tasks: assignment through submission and review.

See SurveyTask's docstring in app.models.tables for what this is and why it
is separate from just registering a parcel (Parcel/ParcelCreate, in
app.routers.parcels) or filing a document (app.routers.documents).
"""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import Role, SurveyTaskStatus
from app.dependencies import entitled_case_ids, get_current_user, get_db, require_role, scope_cases_to_user
from app.models import Case, Parcel, SurveyPhoto, SurveyTask, User
from app.schemas.common import Message
from app.schemas.survey import (
    AssignableOfficerOut,
    SurveyPhotoOut,
    SurveyReviewRequest,
    SurveyTaskCreate,
    SurveyTaskList,
    SurveyTaskOut,
    SurveyTaskSaveRequest,
)
from app.services import audit, geometry
from app.services.uploads import save_upload_file

router = APIRouter(prefix="/survey-tasks", tags=["survey"])

# Who can be assigned to, and work, a task.
SURVEY_PERFORMERS = (Role.ADMIN, Role.FIELD_OFFICER)
# Who can target ANOTHER officer when creating one, and who can review one.
# The same list serves both roles in this prototype: whoever can hand a
# survey to a named field officer is also who decides whether it came back
# right.
SURVEY_ASSIGNERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)
SURVEY_REVIEWERS = SURVEY_ASSIGNERS
# The union, for the one endpoint (create) either side can call.
SURVEY_TASK_CREATORS = (Role.ADMIN, Role.FIELD_OFFICER, Role.DISTRICT_OFFICER, Role.SLAO)

PHOTO_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

# A returned task is editable again — sending it back is a request for
# correction, not a dead end.
EDITABLE_STATUSES = (SurveyTaskStatus.IN_PROGRESS, SurveyTaskStatus.RETURNED)


def _task_or_404(db: Session, user: User, task_id: int) -> SurveyTask:
    task = db.get(SurveyTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Survey task not found")
    if task.assigned_to_user_id == user.id:
        return task
    # Not the assignee: visible only to a reviewer who can also see the
    # underlying case — a field officer in the same district as another
    # officer's task must not see it just because the case would otherwise
    # be in their scope.
    if user.role not in SURVEY_REVIEWERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Survey task not found")
    visible_case = scope_cases_to_user(db.query(Case), user).filter(Case.id == task.case_id).first()
    if visible_case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Survey task not found")
    return task


def _task_out(db: Session, task: SurveyTask) -> SurveyTaskOut:
    case = db.get(Case, task.case_id)
    parcel = db.get(Parcel, task.parcel_id) if task.parcel_id else None
    assigned_to = db.get(User, task.assigned_to_user_id)
    assigned_by = db.get(User, task.assigned_by_user_id) if task.assigned_by_user_id else None
    reviewed_by = db.get(User, task.reviewed_by_user_id) if task.reviewed_by_user_id else None

    point_count = 0
    if task.boundary_geom is not None:
        # The stored ring repeats its first point to close itself; that
        # point is not a corner the officer walked, so it is not counted.
        n_points = db.query(func.ST_NPoints(SurveyTask.boundary_geom)).filter(SurveyTask.id == task.id).scalar()
        point_count = max((n_points or 1) - 1, 0)

    photos = (
        db.query(SurveyPhoto)
        .filter(SurveyPhoto.survey_task_id == task.id)
        .order_by(SurveyPhoto.id)
        .all()
    )

    return SurveyTaskOut(
        id=task.id,
        case_id=task.case_id,
        case_number=case.case_number,
        parcel_id=task.parcel_id,
        parcel_survey_number=parcel.survey_number if parcel else None,
        project_name=case.project.name,
        village_name=case.village.name,
        assigned_to_user_id=task.assigned_to_user_id,
        assigned_to_name=assigned_to.full_name,
        assigned_by_user_id=task.assigned_by_user_id,
        assigned_by_name=assigned_by.full_name if assigned_by else None,
        status=task.status,
        due_on=task.due_on,
        notes=task.notes,
        created_at=task.created_at,
        started_at=task.started_at,
        measured_area_ha=task.measured_area_ha,
        boundary_point_count=point_count,
        has_location=task.location_geom is not None,
        remarks=task.remarks,
        submitted_at=task.submitted_at,
        reviewed_by_name=reviewed_by.full_name if reviewed_by else None,
        reviewed_at=task.reviewed_at,
        review_note=task.review_note,
        photos=[SurveyPhotoOut.model_validate(p) for p in photos],
    )


# Declared before GET /{task_id} so this literal path is matched first —
# the same ordering rule app.routers.parcels documents for /bbox and
# /search, otherwise "officers" would be read as a task id.
@router.get("/officers", response_model=list[AssignableOfficerOut])
def list_assignable_officers(
    district_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_ASSIGNERS)),
):
    """Field officers in one district, for the assign-survey picker."""
    if user.role != Role.ADMIN and user.district_id != district_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot look up officers outside your district")

    rows = (
        db.query(User)
        .filter(User.role == Role.FIELD_OFFICER, User.district_id == district_id, User.is_active.is_(True))
        .order_by(User.full_name)
        .all()
    )
    return [AssignableOfficerOut(id=r.id, full_name=r.full_name, username=r.username) for r in rows]


@router.get("", response_model=SurveyTaskList)
def list_survey_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status_filter: SurveyTaskStatus | None = Query(default=None, alias="status"),
    case_id: int | None = None,
):
    """A field officer sees only their own tasks. A reviewer (SLAO, District
    Officer, Admin) sees every task on a case in their scope — the same
    entitled_case_ids scoping every other list in this API uses."""
    query = db.query(SurveyTask)
    if user.role in SURVEY_REVIEWERS:
        entitled = entitled_case_ids(db, user)
        if entitled is not None:
            if not entitled:
                return SurveyTaskList(items=[], total=0)
            query = query.filter(SurveyTask.case_id.in_(entitled))
    else:
        query = query.filter(SurveyTask.assigned_to_user_id == user.id)

    if status_filter is not None:
        query = query.filter(SurveyTask.status == status_filter)
    if case_id is not None:
        query = query.filter(SurveyTask.case_id == case_id)

    rows = query.order_by(SurveyTask.created_at.desc()).all()
    items = [_task_out(db, row) for row in rows]
    return SurveyTaskList(items=items, total=len(items))


@router.post("", response_model=SurveyTaskOut, status_code=status.HTTP_201_CREATED)
def create_survey_task(
    payload: SurveyTaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_TASK_CREATORS)),
):
    """Either path converges here: a field officer self-starting one from
    their own work queue, or a supervisor assigning one to a named officer.
    """
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == payload.case_id).first()
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Case not found")

    parcel = None
    if payload.parcel_id is not None:
        parcel = (
            db.query(Parcel)
            .filter(Parcel.id == payload.parcel_id, Parcel.case_id == case.id)
            .first()
        )
        if parcel is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Parcel not found on this case")

    assigned_to_id = payload.assigned_to_user_id or user.id
    self_started = assigned_to_id == user.id

    if not self_started:
        if user.role not in SURVEY_ASSIGNERS:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="Only a supervisor can assign a survey to another officer"
            )
        target = db.get(User, assigned_to_id)
        if target is None or target.role != Role.FIELD_OFFICER:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Target must be a field officer")
        if user.role != Role.ADMIN and target.district_id != case.district_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="That officer is not in this case's district"
            )
    elif user.role not in SURVEY_PERFORMERS:
        # A district officer or SLAO assigning themselves makes no sense —
        # only a field officer (or admin, for testing) actually surveys.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a field officer can self-start a survey")

    now = datetime.now(timezone.utc)
    task = SurveyTask(
        case_id=case.id,
        parcel_id=parcel.id if parcel else None,
        assigned_to_user_id=assigned_to_id,
        assigned_by_user_id=None if self_started else user.id,
        status=SurveyTaskStatus.IN_PROGRESS if self_started else SurveyTaskStatus.ASSIGNED,
        started_at=now if self_started else None,
        due_on=payload.due_on,
        notes=payload.notes,
    )
    db.add(task)
    db.flush()

    audit.record(
        db,
        user,
        action="survey_task.create",
        entity_type="survey_task",
        entity_id=task.id,
        detail=f"case={case.id} assigned_to={assigned_to_id} self_started={self_started}",
    )
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.get("/{task_id}", response_model=SurveyTaskOut)
def get_survey_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _task_or_404(db, user, task_id)
    return _task_out(db, task)


@router.post("/{task_id}/start", response_model=SurveyTaskOut)
def start_survey_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_PERFORMERS)),
):
    task = _task_or_404(db, user, task_id)
    if task.assigned_to_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This survey is not assigned to you")
    if task.status != SurveyTaskStatus.ASSIGNED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot start a task that is {task.status.value}"
        )

    task.status = SurveyTaskStatus.IN_PROGRESS
    task.started_at = datetime.now(timezone.utc)
    audit.record(db, user, action="survey_task.start", entity_type="survey_task", entity_id=task.id)
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.patch("/{task_id}", response_model=SurveyTaskOut)
def save_survey_task(
    task_id: int,
    payload: SurveyTaskSaveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_PERFORMERS)),
):
    """Autosave whatever the officer has filled in so far — the measured
    area, the walked boundary, the current-location reading, remarks.
    Everything here is optional per call so the entry portal can save one
    field at a time."""
    task = _task_or_404(db, user, task_id)
    if task.assigned_to_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This survey is not assigned to you")
    if task.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot edit a task that is {task.status.value}"
        )

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    if "measured_area_ha" in fields:
        task.measured_area_ha = payload.measured_area_ha
    if "boundary_points" in fields:
        points = payload.boundary_points
        if points is None:
            task.boundary_geom = None
        else:
            distinct = {(round(p.latitude, 6), round(p.longitude, 6)) for p in points}
            if len(distinct) < 3:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="A boundary needs at least 3 distinct corners"
                )
            task.boundary_geom = geometry.polygon_ewkt(points)
    if "location" in fields:
        loc = payload.location
        task.location_geom = geometry.point_ewkt(loc.latitude, loc.longitude) if loc else None
    if "remarks" in fields:
        task.remarks = payload.remarks

    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.post("/{task_id}/photos", response_model=SurveyPhotoOut, status_code=status.HTTP_201_CREATED)
async def upload_survey_photo(
    task_id: int,
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    caption: str | None = Form(default=None, max_length=200),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_PERFORMERS)),
):
    task = _task_or_404(db, user, task_id)
    if task.assigned_to_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This survey is not assigned to you")
    if task.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot add a photo to a task that is {task.status.value}"
        )

    saved = await save_upload_file(file, PHOTO_CONTENT_TYPES)
    photo = SurveyPhoto(
        survey_task_id=task.id,
        stored_name=saved.stored_name,
        content_type=file.content_type,
        size_bytes=saved.size_bytes,
        latitude=latitude,
        longitude=longitude,
        caption=caption,
        uploaded_by_user_id=user.id,
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    return photo


@router.get("/{task_id}/photos/{photo_id}")
def download_survey_photo(
    task_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = _task_or_404(db, user, task_id)
    photo = (
        db.query(SurveyPhoto)
        .filter(SurveyPhoto.id == photo_id, SurveyPhoto.survey_task_id == task.id)
        .first()
    )
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")

    path = Path(settings.upload_dir) / photo.stored_name
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo file not found")
    return FileResponse(path, media_type=photo.content_type)


@router.delete("/{task_id}/photos/{photo_id}", response_model=Message)
def delete_survey_photo(
    task_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_PERFORMERS)),
):
    task = _task_or_404(db, user, task_id)
    if task.assigned_to_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This survey is not assigned to you")
    if task.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot remove a photo from a task that is {task.status.value}"
        )

    photo = (
        db.query(SurveyPhoto)
        .filter(SurveyPhoto.id == photo_id, SurveyPhoto.survey_task_id == task.id)
        .first()
    )
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")

    (Path(settings.upload_dir) / photo.stored_name).unlink(missing_ok=True)
    db.delete(photo)
    db.commit()
    return Message(detail="Photo removed.")


@router.post("/{task_id}/submit", response_model=SurveyTaskOut)
def submit_survey_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_PERFORMERS)),
):
    task = _task_or_404(db, user, task_id)
    if task.assigned_to_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This survey is not assigned to you")
    if task.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot submit a task that is {task.status.value}"
        )

    has_photo = (
        db.query(SurveyPhoto.id).filter(SurveyPhoto.survey_task_id == task.id).first() is not None
    )
    if task.measured_area_ha is None and task.boundary_geom is None and not has_photo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Record a measured area, a boundary, or at least one photo before submitting",
        )

    # The one place in the whole system that ever writes Parcel.boundary —
    # every other parcel stays a bare GPS point until a survey walks it.
    if task.boundary_geom is not None and task.parcel_id is not None:
        parcel = db.get(Parcel, task.parcel_id)
        parcel.boundary = task.boundary_geom

    task.status = SurveyTaskStatus.SUBMITTED
    task.submitted_at = datetime.now(timezone.utc)
    audit.record(db, user, action="survey_task.submit", entity_type="survey_task", entity_id=task.id)
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.post("/{task_id}/approve", response_model=SurveyTaskOut)
def approve_survey_task(
    task_id: int,
    payload: SurveyReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_REVIEWERS)),
):
    task = _task_or_404(db, user, task_id)
    if task.status != SurveyTaskStatus.SUBMITTED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot approve a task that is {task.status.value}"
        )

    task.status = SurveyTaskStatus.APPROVED
    task.reviewed_by_user_id = user.id
    task.reviewed_at = datetime.now(timezone.utc)
    task.review_note = payload.review_note.strip() if payload.review_note else None
    audit.record(
        db, user, action="survey_task.approve", entity_type="survey_task", entity_id=task.id,
        detail=task.review_note or "",
    )
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.post("/{task_id}/return", response_model=SurveyTaskOut)
def return_survey_task(
    task_id: int,
    payload: SurveyReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*SURVEY_REVIEWERS)),
):
    task = _task_or_404(db, user, task_id)
    if task.status != SurveyTaskStatus.SUBMITTED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot return a task that is {task.status.value}"
        )
    if not payload.review_note or len(payload.review_note.strip()) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="A reason is required when returning a survey")

    task.status = SurveyTaskStatus.RETURNED
    task.reviewed_by_user_id = user.id
    task.reviewed_at = datetime.now(timezone.utc)
    task.review_note = payload.review_note.strip()
    audit.record(
        db, user, action="survey_task.return", entity_type="survey_task", entity_id=task.id,
        detail=task.review_note,
    )
    db.commit()
    db.refresh(task)
    return _task_out(db, task)
