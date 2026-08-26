"""Dashboard routes — the five KPI tiles, the alerts panel, and the
cases-by-stage breakdown.

Everything here reads from app.ai_layer. The AI Layer owns the
calculations; this router owns who may ask for them and the shape the
answer is published in.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case as sql_case
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai_layer.kpis import compute_kpis
from app.core.enums import AlertSeverity, Stage
from app.dependencies import entitled_case_ids, get_current_user, get_db
from app.models import Alert, Case, User
from app.schemas.dashboard import AlertList, AlertOut, DashboardKpis, StageBreakdownItem

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# The severity enum stores as text, so ordering by the column would sort
# alphabetically and put "critical" after "high". This ranks it in SQL, so
# worst-first ordering happens in the database and the API only ever
# materialises the page it is about to return.
SEVERITY_RANK = sql_case(
    {
        AlertSeverity.CRITICAL: 0,
        AlertSeverity.HIGH: 1,
        AlertSeverity.MEDIUM: 2,
        AlertSeverity.LOW: 3,
    },
    value=Alert.severity,
    else_=9,
)


@router.get("/kpis", response_model=DashboardKpis)
def dashboard_kpis(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    district_id: int | None = None,
    project_id: int | None = None,
):
    """The five numbers named by the problem statement.

    Compensation and R&R are reported separately and must never be added
    together: a tenant farmer can be owed resettlement while receiving no
    land compensation at all.
    """
    try:
        return DashboardKpis(
            **compute_kpis(
                db,
                district_id=district_id,
                project_id=project_id,
                base_case_ids=entitled_case_ids(db, user),
            )
        )
    except ValueError as exc:
        # An unknown district or project is a bad request, not an empty
        # dashboard — returning zeros would look like real data.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/alerts", response_model=AlertList)
def dashboard_alerts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    severity: AlertSeverity | None = None,
    rule: str | None = Query(default=None, max_length=60),
    include_resolved: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Alerts for the cases this user may see, worst first.

    Populated by POST /admin/run-rules. An empty list means the rules have
    not been run yet, not that nothing is wrong.
    """
    entitled = entitled_case_ids(db, user)
    if entitled is not None and not entitled:
        return AlertList(items=[], total=0, by_severity={}, by_rule={})

    def scoped(query):
        """Apply the entitlement and the caller's filters to any query."""
        if entitled is not None:
            query = query.filter(Alert.case_id.in_(entitled))
        if not include_resolved:
            query = query.filter(Alert.is_resolved.is_(False))
        if severity is not None:
            query = query.filter(Alert.severity == severity)
        if rule is not None:
            query = query.filter(Alert.rule == rule)
        return query

    # Totals are aggregated in the database. Counting them by walking the
    # full result set in Python would mean loading every alert just to
    # return one page of them.
    total = scoped(db.query(func.count(Alert.id))).scalar() or 0
    by_severity = {
        sev.value: count
        for sev, count in scoped(db.query(Alert.severity, func.count(Alert.id)))
        .group_by(Alert.severity)
        .all()
    }
    by_rule = {
        rule_name: count
        for rule_name, count in scoped(db.query(Alert.rule, func.count(Alert.id)))
        .group_by(Alert.rule)
        .all()
    }

    rows = (
        scoped(db.query(Alert, Case.case_number, Case.district_id, Case.stage))
        .join(Case, Alert.case_id == Case.id)
        .order_by(SEVERITY_RANK, Alert.case_id, Alert.id)
        .limit(limit)
        .all()
    )

    items = [
        AlertOut(
            id=alert.id,
            case_id=alert.case_id,
            case_number=case_number,
            district_id=district_id,
            stage=stage,
            rule=alert.rule,
            severity=alert.severity,
            message=alert.message,
            detected_on=alert.detected_on,
            details=alert.details or {},
            is_resolved=alert.is_resolved,
        )
        for alert, case_number, district_id, stage in rows
    ]
    return AlertList(items=items, total=total, by_severity=by_severity, by_rule=by_rule)


@router.get("/cases-by-stage", response_model=list[StageBreakdownItem])
def cases_by_stage(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """How many cases sit at each of the nine legal stages.

    Every stage is returned, including empty ones, so the chart keeps a
    stable set of bars instead of silently dropping categories.
    """
    entitled = entitled_case_ids(db, user)
    counts = {stage: 0 for stage in Stage}
    if entitled is None or entitled:
        query = db.query(Case.stage, func.count(Case.id))
        if entitled is not None:
            query = query.filter(Case.id.in_(entitled))
        for stage, count in query.group_by(Case.stage).all():
            counts[stage] = count

    return [StageBreakdownItem(stage=stage, case_count=counts[stage]) for stage in Stage]
