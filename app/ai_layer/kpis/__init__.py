"""The dashboard KPI contract: `compute_kpis` and the scope resolver it and
the trend/forecast routes share.

Every figure here is a SQL aggregate over case-scoped rows, computed fresh
on every call -- nothing is a stored total (see app.routers.dashboard). Each
submodule owns one group of related fields so the queries stay next to the
schema they read, and `compute_kpis` only assembles their output.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.ai_layer.kpis.area import area_kpis
from app.ai_layer.kpis.compensation import compensation_kpis
from app.ai_layer.kpis.families import families_kpis
from app.ai_layer.kpis.notices import notices_kpis
from app.ai_layer.kpis.possession import possession_kpis
from app.ai_layer.kpis.rnr import rnr_kpis
from app.ai_layer.kpis.timeline import timeline_kpis
from app.models import Case, District, Project, State

__all__ = ["compute_kpis", "resolve_scope"]


def resolve_scope(
    db: Session,
    *,
    state_id: int | None = None,
    district_id: int | None = None,
    project_id: int | None = None,
    base_case_ids: list[int] | None = None,
) -> list[int]:
    """Case ids matching every filter given, intersected with the caller's
    entitlement.

    `base_case_ids=None` means unrestricted (see
    `app.dependencies.entitled_case_ids`); `base_case_ids=[]` means entitled
    to nothing, and short-circuits without touching the database. An unknown
    state, district or project raises `ValueError` -- a bad filter must read
    as a bad request, not silently return an empty dashboard.
    """
    if base_case_ids is not None and not base_case_ids:
        return []

    query = db.query(Case.id)

    if state_id is not None:
        if db.query(State.id).filter(State.id == state_id).first() is None:
            raise ValueError(f"Unknown state_id {state_id}")
        query = query.join(District, Case.district_id == District.id).filter(
            District.state_id == state_id
        )

    if district_id is not None:
        if db.query(District.id).filter(District.id == district_id).first() is None:
            raise ValueError(f"Unknown district_id {district_id}")
        query = query.filter(Case.district_id == district_id)

    if project_id is not None:
        if db.query(Project.id).filter(Project.id == project_id).first() is None:
            raise ValueError(f"Unknown project_id {project_id}")
        query = query.filter(Case.project_id == project_id)

    if base_case_ids is not None:
        query = query.filter(Case.id.in_(base_case_ids))

    return [case_id for (case_id,) in query.all()]


def compute_kpis(
    db: Session,
    *,
    state_id: int | None = None,
    district_id: int | None = None,
    project_id: int | None = None,
    base_case_ids: list[int] | None = None,
    as_of: date | None = None,
) -> dict:
    """Every number named by the problem statement, for one scope.

    Compensation and R&R are reported separately and must never be summed:
    a tenant farmer can be owed resettlement while receiving no land
    compensation at all.
    """
    as_of = as_of or date.today()
    case_ids = resolve_scope(
        db,
        state_id=state_id,
        district_id=district_id,
        project_id=project_id,
        base_case_ids=base_case_ids,
    )

    result: dict = {
        "scope": {
            "state_id": state_id,
            "district_id": district_id,
            "project_id": project_id,
            "case_count": len(case_ids),
        }
    }
    result.update(area_kpis(db, case_ids))
    result.update(compensation_kpis(db, case_ids))
    result.update(families_kpis(db, case_ids))
    result.update(rnr_kpis(db, case_ids))
    result.update(possession_kpis(db, case_ids))
    result.update(timeline_kpis(db, case_ids, as_of))
    result.update(notices_kpis(db, case_ids))
    return result
