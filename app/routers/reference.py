"""Districts, villages and projects — the lists Frontend's dropdowns need.

Authenticated but not role-restricted: these are public record and carry no
personal data, and a landowner filing an objection still needs to see the
name of the project affecting them.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models import Case, District, Project, State, User, Village
from app.schemas.reference import DistrictOut, ProjectOut, StateOut, VillageOut

router = APIRouter(tags=["reference"])


@router.get("/states", response_model=list[StateOut])
def list_states(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """States and Union Territories, with how much is happening in each.

    The list the national dashboard's top-level filter is built from. Counts
    come from two grouped queries rather than one per state.
    """
    district_counts = dict(
        db.query(District.state_id, func.count(District.id)).group_by(District.state_id).all()
    )
    case_counts = dict(
        db.query(District.state_id, func.count(Case.id))
        .join(Case, Case.district_id == District.id)
        .group_by(District.state_id)
        .all()
    )
    return [
        StateOut(
            id=state.id,
            name=state.name,
            code=state.code,
            lgd_code=state.lgd_code,
            is_union_territory=state.is_union_territory,
            district_count=district_counts.get(state.id, 0),
            case_count=case_counts.get(state.id, 0),
        )
        for state in db.query(State).order_by(State.name).all()
    ]


@router.get("/districts", response_model=list[DistrictOut])
def list_districts(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    state_id: int | None = None,
):
    """Districts, optionally narrowed to one state.

    `state` is still the state NAME, so existing callers keep working;
    `state_id` is published alongside for anything that filters by it.
    """
    query = db.query(District, State).join(State, District.state_id == State.id)
    if state_id is not None:
        query = query.filter(District.state_id == state_id)

    return [
        DistrictOut(
            id=district.id,
            name=district.name,
            state=state.name,
            state_id=state.id,
            code=district.code,
            lgd_code=district.lgd_code,
        )
        for district, state in query.order_by(State.name, District.name).all()
    ]


@router.get("/villages", response_model=list[VillageOut])
def list_villages(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    district_id: int | None = None,
):
    """Villages, optionally narrowed to one district — which is how the
    create-case form should use it, so the picker only offers villages
    that belong to the district already chosen."""
    query = db.query(Village, District.name).join(District, Village.district_id == District.id)
    if district_id is not None:
        query = query.filter(Village.district_id == district_id)

    return [
        VillageOut(
            id=village.id,
            name=village.name,
            district_id=village.district_id,
            district_name=district_name,
            lgd_code=village.lgd_code,
        )
        for village, district_name in query.order_by(District.name, Village.name).all()
    ]


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    district_id: int | None = None,
):
    # Case counts come from one grouped query rather than one per project.
    counts = dict(db.query(Case.project_id, func.count(Case.id)).group_by(Case.project_id).all())

    query = db.query(Project, District.name).join(District, Project.district_id == District.id)
    if district_id is not None:
        query = query.filter(Project.district_id == district_id)

    return [
        ProjectOut(
            id=project.id,
            name=project.name,
            requiring_body=project.requiring_body,
            district_id=project.district_id,
            district_name=district_name,
            case_count=counts.get(project.id, 0),
        )
        for project, district_name in query.order_by(Project.name).all()
    ]
