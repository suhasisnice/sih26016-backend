from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.enums import CaseStatus, Role, Stage
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.models import (
    AuditLog,
    Case,
    CaseStageHistory,
    District,
    Document,
    Objection,
    Parcel,
    Project,
    User,
    Village,
)
from app.schemas import (
    CaseCreate,
    CaseDetail,
    CaseListItem,
    CaseStageAdvance,
    CaseStageHistoryOut,
    PaginatedCases,
)
from app.schemas.audit import AuditEntryOut, AuditList
from app.services import audit, numbering, workflow

router = APIRouter(prefix="/cases", tags=["cases"])

# Roles allowed to create a case or move one along. A landowner is not one
# of them: they may see and object, not administer.
CASE_WRITERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)

# The audit trail names which officers acted on a case — useful to other
# officers, not something a landowner needs to follow their own acquisition.
CASE_AUDIT_READERS = (
    Role.ADMIN,
    Role.DISTRICT_OFFICER,
    Role.SLAO,
    Role.FIELD_OFFICER,
    Role.RNR_OFFICER,
)


def _parcel_totals(db: Session, case_ids: list[int]) -> dict[int, tuple[int, float]]:
    """Parcel count and total hectares per case, in one grouped query
    rather than one query per row."""
    if not case_ids:
        return {}
    rows = (
        db.query(Parcel.case_id, func.count(Parcel.id), func.coalesce(func.sum(Parcel.area_ha), 0.0))
        .filter(Parcel.case_id.in_(case_ids))
        .group_by(Parcel.case_id)
        .all()
    )
    return {case_id: (count, round(float(area), 4)) for case_id, count, area in rows}


@router.get("", response_model=PaginatedCases)
def list_cases(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    stage: Stage | None = None,
    case_status: CaseStatus | None = None,
    district_id: int | None = None,
    project_id: int | None = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Case table, filtered and paginated.

    Scoped to what this user may see before any of their own filters are
    applied, so narrowing can only ever shrink the result, never widen it
    past their entitlement.
    """
    query = (
        db.query(Case, District.name, Village.name, Project.name)
        .join(District, Case.district_id == District.id)
        .join(Village, Case.village_id == Village.id)
        .join(Project, Case.project_id == Project.id)
    )
    query = scope_cases_to_user(query, user)

    if stage is not None:
        query = query.filter(Case.stage == stage)
    if case_status is not None:
        query = query.filter(Case.status == case_status)
    if district_id is not None:
        query = query.filter(Case.district_id == district_id)
    if project_id is not None:
        query = query.filter(Case.project_id == project_id)
    if search:
        # ilike with a bound parameter — the wildcards are ours, the value
        # stays parameterised, so a % or _ in user input cannot alter the
        # query's structure.
        pattern = f"%{search}%"
        query = query.filter(Case.case_number.ilike(pattern) | Case.title.ilike(pattern))

    total = query.order_by(None).count()
    rows = query.order_by(Case.stage_changed_at.asc(), Case.id.asc()).limit(limit).offset(offset).all()

    totals = _parcel_totals(db, [row[0].id for row in rows])
    today = date.today()

    items = []
    for case, district_name, village_name, project_name in rows:
        parcel_count, total_area = totals.get(case.id, (0, 0.0))
        items.append(
            CaseListItem(
                id=case.id,
                case_number=case.case_number,
                title=case.title,
                stage=case.stage,
                status=case.status,
                district_id=case.district_id,
                district_name=district_name,
                village_name=village_name,
                project_name=project_name,
                stage_changed_at=case.stage_changed_at,
                days_in_stage=(today - case.stage_changed_at).days,
                parcel_count=parcel_count,
                total_area_ha=total_area,
            )
        )

    return PaginatedCases(items=items, total=total, limit=limit, offset=offset)


def _get_visible_case(db: Session, user: User, case_id: int) -> Case:
    """Fetch a case the user is entitled to see, or 404.

    Returns 404 rather than 403 for a case outside their scope: a 403 would
    confirm the case exists, which is itself information a landowner should
    not be able to fish for by trying ids.
    """
    query = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id)
    case = query.first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(case_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    case = _get_visible_case(db, user, case_id)
    parcel_count, total_area = _parcel_totals(db, [case.id]).get(case.id, (0, 0.0))
    history = (
        db.query(CaseStageHistory)
        .filter(CaseStageHistory.case_id == case.id)
        .order_by(CaseStageHistory.changed_on.asc(), CaseStageHistory.id.asc())
        .all()
    )

    return CaseDetail(
        id=case.id,
        case_number=case.case_number,
        title=case.title,
        stage=case.stage,
        status=case.status,
        project_id=case.project_id,
        project_name=case.project.name,
        district_id=case.district_id,
        district_name=case.district.name,
        village_id=case.village_id,
        village_name=case.village.name,
        stage_changed_at=case.stage_changed_at,
        created_at=case.created_at,
        days_in_stage=(date.today() - case.stage_changed_at).days,
        parcel_count=parcel_count,
        total_area_ha=total_area,
        allowed_next_stages=workflow.allowed_transitions(case.stage),
        stage_history=[CaseStageHistoryOut.model_validate(h) for h in history],
    )


@router.post("", response_model=CaseDetail, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
):
    village = db.get(Village, payload.village_id)
    if village is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown village_id")
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown project_id")

    # District comes from the village, never from the client.
    district_id = village.district_id
    if project.district_id != district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project and village belong to different districts",
        )
    if user.role is not Role.ADMIN and user.district_id is not None and user.district_id != district_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create a case outside your district",
        )

    district = db.get(District, district_id)
    today = date.today()
    case_number = numbering.next_case_number(db, district, today.year)

    case = Case(
        case_number=case_number,
        title=payload.title,
        project_id=payload.project_id,
        district_id=district_id,
        village_id=payload.village_id,
        stage=Stage.PRELIMINARY_NOTIFICATION,
        status=CaseStatus.ACTIVE,
        stage_changed_at=today,
        created_at=today,
    )
    db.add(case)
    db.flush()

    db.add(
        CaseStageHistory(
            case_id=case.id,
            from_stage=None,
            to_stage=Stage.PRELIMINARY_NOTIFICATION,
            changed_by_user_id=user.id,
            changed_on=today,
            note="Case opened",
        )
    )
    audit.record(
        db, user, action="case.create", entity_type="case", entity_id=case.id, detail=case_number
    )
    db.commit()

    return get_case(case.id, db=db, user=user)


@router.get("/{case_id}/audit", response_model=AuditList)
def case_audit_trail(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_AUDIT_READERS)),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Who did what to this case, and when.

    Officers and admins only. The trail names the officers who acted on a
    case, which is not something a landowner needs in order to follow their
    own acquisition.

    Covers the case itself plus the documents and objections attached to
    it, so the page reads as one history rather than three.
    """
    _get_visible_case(db, user, case_id)

    document_ids = [d for (d,) in db.query(Document.id).filter(Document.case_id == case_id).all()]
    objection_ids = [o for (o,) in db.query(Objection.id).filter(Objection.case_id == case_id).all()]

    conditions = [(AuditLog.entity_type == "case") & (AuditLog.entity_id == case_id)]
    if document_ids:
        conditions.append(
            (AuditLog.entity_type == "document") & (AuditLog.entity_id.in_(document_ids))
        )
    if objection_ids:
        conditions.append(
            (AuditLog.entity_type == "objection") & (AuditLog.entity_id.in_(objection_ids))
        )

    query = (
        db.query(AuditLog, User.full_name)
        .outerjoin(User, AuditLog.user_id == User.id)
        .filter(or_(*conditions))
    )
    total = query.count()
    rows = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all()

    return AuditList(
        items=[
            AuditEntryOut(
                id=entry.id,
                user_id=entry.user_id,
                user_name=user_name,
                action=entry.action,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                detail=entry.detail,
                created_at=entry.created_at,
            )
            for entry, user_name in rows
        ],
        total=total,
    )


@router.post("/{case_id}/advance", response_model=CaseDetail)
def advance_stage(
    case_id: int,
    payload: CaseStageAdvance,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
):
    """Move a case to the next (or previous) legal stage.

    workflow.advance_case refuses anything the Act does not allow and
    writes both the stage history and the audit entry.
    """
    case = _get_visible_case(db, user, case_id)
    workflow.advance_case(db, case, payload.to_stage, user, note=payload.note)
    db.commit()
    return get_case(case.id, db=db, user=user)
