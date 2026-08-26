"""The five dashboard numbers named by the problem statement.

compute_kpis() is what the dashboard route calls. It resolves the scope
once and hands the resulting case ids to each calculation, so every number
on the dashboard describes exactly the same set of cases.
"""

from sqlalchemy.orm import Session

from app.ai_layer.kpis.area import compute_area
from app.ai_layer.kpis.compensation import compute_compensation
from app.ai_layer.kpis.families import compute_families
from app.ai_layer.kpis.possession import compute_possession
from app.ai_layer.kpis.rnr import compute_rnr
from app.models import Case, District, Project


def resolve_scope(
    db: Session,
    district_id: int | None,
    project_id: int | None,
    base_case_ids: list[int] | None = None,
) -> list[int]:
    """Resolve filters to a concrete set of case ids.

    An unrecognised district or project raises rather than being ignored.
    Silently dropping a filter we do not understand would answer a narrow
    question with national totals — on a screen a role-restricted officer
    is looking at, that means showing them figures they are not entitled
    to. Failing loudly is the safe direction.

    base_case_ids is the caller's entitlement, already computed. Filters
    intersect with it and can only ever narrow it further.
    """
    if district_id is not None and not db.get(District, district_id):
        raise ValueError(f"unknown district_id: {district_id}")
    if project_id is not None and not db.get(Project, project_id):
        raise ValueError(f"unknown project_id: {project_id}")

    query = db.query(Case.id)
    if base_case_ids is not None:
        query = query.filter(Case.id.in_(base_case_ids))
    if district_id is not None:
        query = query.filter(Case.district_id == district_id)
    if project_id is not None:
        query = query.filter(Case.project_id == project_id)
    return [case_id for (case_id,) in query.all()]


def compute_kpis(
    db: Session,
    district_id: int | None = None,
    project_id: int | None = None,
    base_case_ids: list[int] | None = None,
) -> dict:
    """All five dashboard numbers for the given scope.

    Who is allowed to ask for which scope is the route's call, enforced by
    passing base_case_ids. This function computes whatever it is asked for
    and enforces nothing itself.
    """
    case_ids = resolve_scope(db, district_id, project_id, base_case_ids)

    return {
        "scope": {
            "district_id": district_id,
            "project_id": project_id,
            "case_count": len(case_ids),
        },
        **compute_area(db, case_ids),
        **compute_compensation(db, case_ids),
        **compute_families(db, case_ids),
        **compute_rnr(db, case_ids),
        **compute_possession(db, case_ids),
    }
