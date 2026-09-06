"""The Projects workspace — a project-level rollup over the cases beneath
it.

Project itself carries no status or progress of its own (see
app.schemas.reference.ProjectOut) — everything here is computed live from
the cases, parcels and alerts under it, never stored, for the same reason
CaseDetail's consent_obtained_pct is computed rather than cached.

Case-level detail (documents, objections, compensation, timeline) already
has a complete workspace at GET /cases/{id} — this router does not repeat
any of it. A project's "workspace" is this rollup plus the ordinary case
list filtered to the project (GET /cases?project_id=...), not a second copy
of everything a case detail page already shows.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.enums import ParcelStatus, TimelineStatus
from app.dependencies import entitled_case_ids, get_current_user, get_db
from app.models import Alert, Case, CaseStageHistory, District, Parcel, Project, User
from app.schemas.project import ProjectWorkspaceList, ProjectWorkspaceOut
from app.services import provenance, sla

# Not "/projects" — app.routers.reference already owns that path for the
# plain unscoped dropdown list every create-form uses (GET /projects,
# registered with no prefix at all). This is a different, heavier,
# user-scoped rollup for the Projects workspace page specifically, so it
# gets its own namespace rather than silently colliding with — and, by
# router registration order, losing to — the existing route.
router = APIRouter(prefix="/project-workspaces", tags=["projects"])

_DEADLINE_RANK = {
    TimelineStatus.BREACHED: 0,
    TimelineStatus.AT_RISK: 1,
    TimelineStatus.ON_TIME: 2,
}


def _rollup(db: Session, project: Project, district_name: str, case_ids: list[int]) -> ProjectWorkspaceOut:
    cases = db.query(Case).filter(Case.id.in_(case_ids)).all() if case_ids else []

    required_area = 0.0
    affected_area = 0.0
    if case_ids:
        required_area = float(
            db.query(func.coalesce(func.sum(Parcel.area_ha), 0.0))
            .filter(Parcel.case_id.in_(case_ids))
            .scalar()
            or 0.0
        )
        affected_area = float(
            db.query(func.coalesce(func.sum(Parcel.area_ha), 0.0))
            .filter(
                Parcel.case_id.in_(case_ids),
                Parcel.status.in_([ParcelStatus.ACQUIRED, ParcelStatus.POSSESSION_TAKEN]),
            )
            .scalar()
            or 0.0
        )

    stages = {c.stage for c in cases}
    current_stage = stages.pop() if len(stages) == 1 else None

    today = date.today()
    sla_table = sla.load_sla(db)
    worst_deadline = TimelineStatus.ON_TIME
    for c in cases:
        status_ = sla.timeline_status(c.stage_due_on, c.stage, today, sla_table)
        if _DEADLINE_RANK[status_] < _DEADLINE_RANK[worst_deadline]:
            worst_deadline = status_

    responsible_name = None
    if case_ids:
        last_move = (
            db.query(CaseStageHistory.changed_by_user_id)
            .filter(CaseStageHistory.case_id.in_(case_ids), CaseStageHistory.changed_by_user_id.isnot(None))
            .order_by(CaseStageHistory.changed_on.desc(), CaseStageHistory.id.desc())
            .first()
        )
        if last_move:
            officer = db.get(User, last_move[0])
            responsible_name = officer.full_name if officer else None

    pending_actions = 0
    if case_ids:
        pending_actions = (
            db.query(func.count(Alert.id))
            .filter(Alert.case_id.in_(case_ids), Alert.is_resolved.is_(False))
            .scalar()
            or 0
        )

    return ProjectWorkspaceOut(
        id=project.id,
        name=project.name,
        requiring_body=project.requiring_body,
        district_id=project.district_id,
        district_name=district_name,
        case_count=len(cases),
        required_area_ha=round(required_area, 4),
        affected_area_ha=round(affected_area, 4),
        current_stage=current_stage,
        overall_progress_pct=(
            round(affected_area / required_area * 100, 1) if required_area > 0 else None
        ),
        responsible_officer_name=responsible_name,
        pending_action_count=int(pending_actions),
        deadline_status=worst_deadline,
        provenance=provenance.out(project),
    )


@router.get("", response_model=ProjectWorkspaceList)
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    district_id: int | None = None,
):
    """Every project this user may see, with its own rollup — scoped the
    same way the case list is, so a district officer never sees another
    district's project even though Project itself carries no visibility
    rule of its own."""
    entitled = entitled_case_ids(db, user)

    query = db.query(Project, District.name).join(District, Project.district_id == District.id)
    if district_id is not None:
        query = query.filter(Project.district_id == district_id)
    rows = query.order_by(Project.name).all()

    items = []
    for project, district_name in rows:
        case_query = db.query(Case.id).filter(Case.project_id == project.id)
        if entitled is not None:
            case_query = case_query.filter(Case.id.in_(entitled))
        case_ids = [c for (c,) in case_query.all()]

        # A project with no visible cases at all is not this user's to see —
        # the same fail-closed rule scope_cases_to_user applies everywhere
        # else, just expressed as "skip it" rather than "filter it".
        if entitled is not None and not case_ids:
            continue

        items.append(_rollup(db, project, district_name, case_ids))

    return ProjectWorkspaceList(items=items, total=len(items))


@router.get("/{project_id}", response_model=ProjectWorkspaceOut)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = (
        db.query(Project, District.name)
        .join(District, Project.district_id == District.id)
        .filter(Project.id == project_id)
        .first()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    project, district_name = row

    entitled = entitled_case_ids(db, user)
    case_query = db.query(Case.id).filter(Case.project_id == project.id)
    if entitled is not None:
        case_query = case_query.filter(Case.id.in_(entitled))
    case_ids = [c for (c,) in case_query.all()]

    if entitled is not None and not case_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    return _rollup(db, project, district_name, case_ids)
