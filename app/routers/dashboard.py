"""Dashboard routes — KPI tiles, alerts, stage breakdown, trends, forecast.

Everything here reads from app.ai_layer. The AI Layer owns the calculations;
this router owns who may ask for them and the shape the answer is published
in.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case as sql_case
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai_layer import predict
from app.ai_layer.kpis import compute_kpis, resolve_scope
from app.core.enums import AlertSeverity, CaseStatus, Stage
from app.dependencies import entitled_case_ids, get_current_user, get_db
from app.models import (
    Alert,
    Case,
    CaseStageHistory,
    Compensation,
    Parcel,
    StatutoryNotice,
    User,
)
from app.schemas.dashboard import (
    AlertList,
    AlertOut,
    DashboardKpis,
    ForecastResponse,
    StageBreakdownItem,
    TrendPoint,
    TrendSeries,
)

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
    state_id: int | None = None,
    district_id: int | None = None,
    project_id: int | None = None,
):
    """Every number named by the problem statement, for one scope.

    Compensation and R&R are reported separately and must never be added
    together: a tenant farmer can be owed resettlement while receiving no
    land compensation at all.
    """
    try:
        return DashboardKpis(
            **compute_kpis(
                db,
                state_id=state_id,
                district_id=district_id,
                project_id=project_id,
                base_case_ids=entitled_case_ids(db, user),
            )
        )
    except ValueError as exc:
        # An unknown state, district or project is a bad request, not an
        # empty dashboard — returning zeros would look like real data.
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
    """How many cases sit at each of the nine legal stages, and how many of
    those are past their deadline.

    Every stage is returned, including empty ones, so the chart keeps a
    stable set of bars instead of silently dropping categories.
    """
    entitled = entitled_case_ids(db, user)
    counts = {stage: 0 for stage in Stage}
    breached = {stage: 0 for stage in Stage}

    if entitled is None or entitled:
        today = date.today()
        query = db.query(
            Case.stage,
            func.count(Case.id),
            func.coalesce(
                func.sum(
                    sql_case(
                        (
                            (Case.stage_due_on.isnot(None)) & (Case.stage_due_on < today),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        if entitled is not None:
            query = query.filter(Case.id.in_(entitled))
        for stage, count, over in query.group_by(Case.stage).all():
            counts[stage] = count
            breached[stage] = int(over)

    return [
        StageBreakdownItem(stage=stage, case_count=counts[stage], breached_count=breached[stage])
        for stage in Stage
    ]


@router.get("/trends", response_model=TrendSeries)
def dashboard_trends(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    months: int = Query(default=12, ge=1, le=36),
    state_id: int | None = None,
    district_id: int | None = None,
):
    """Month-by-month progress for the last N months.

    Every other figure on this dashboard is a snapshot of now, which cannot
    answer "are we speeding up or slowing down" — the question a reviewing
    officer actually asks. This adds the time dimension that was missing.

    Aggregated in SQL with date_trunc, one query per series, rather than by
    loading rows and bucketing them in Python.
    """
    try:
        case_ids = resolve_scope(
            db,
            district_id=district_id,
            project_id=None,
            base_case_ids=entitled_case_ids(db, user),
            state_id=state_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    today = date.today()
    # Month arithmetic done in months, not in days. Stepping back
    # 31 * (months - 1) days overshoots whenever the window spans a short
    # month, which silently returned 13 points for a 12-month request.
    total_months = today.year * 12 + (today.month - 1) - (months - 1)
    start = date(total_months // 12, total_months % 12 + 1, 1)

    periods: list[str] = []
    for offset in range(months):
        absolute = total_months + offset
        periods.append(f"{absolute // 12:04d}-{absolute % 12 + 1:02d}")

    empty = {
        period: {
            "cases_opened": 0,
            "cases_closed": 0,
            "stage_transitions": 0,
            "notices_issued": 0,
            "compensation_paid": 0,
            "area_acquired_ha": 0.0,
        }
        for period in periods
    }

    if not case_ids:
        return TrendSeries(
            points=[TrendPoint(period=p, **empty[p]) for p in periods],
            from_period=periods[0] if periods else "",
            to_period=periods[-1] if periods else "",
        )

    def month(column):
        return func.to_char(func.date_trunc("month", column), "YYYY-MM")

    for period, count in (
        db.query(month(Case.created_at), func.count(Case.id))
        .filter(Case.id.in_(case_ids), Case.created_at >= start)
        .group_by(month(Case.created_at))
        .all()
    ):
        if period in empty:
            empty[period]["cases_opened"] = int(count)

    for period, count in (
        db.query(month(Case.stage_changed_at), func.count(Case.id))
        .filter(
            Case.id.in_(case_ids),
            Case.status == CaseStatus.CLOSED,
            Case.stage_changed_at >= start,
        )
        .group_by(month(Case.stage_changed_at))
        .all()
    ):
        if period in empty:
            empty[period]["cases_closed"] = int(count)

    for period, count in (
        db.query(month(CaseStageHistory.changed_on), func.count(CaseStageHistory.id))
        .filter(
            CaseStageHistory.case_id.in_(case_ids),
            CaseStageHistory.changed_on >= start,
        )
        .group_by(month(CaseStageHistory.changed_on))
        .all()
    ):
        if period in empty:
            empty[period]["stage_transitions"] = int(count)

    for period, count in (
        db.query(month(StatutoryNotice.issued_on), func.count(StatutoryNotice.id))
        .filter(
            StatutoryNotice.case_id.in_(case_ids),
            StatutoryNotice.issued_on >= start,
        )
        .group_by(month(StatutoryNotice.issued_on))
        .all()
    ):
        if period in empty:
            empty[period]["notices_issued"] = int(count)

    # Payment dates are not recorded separately from the award date, so this
    # attributes a payment to the month the award was made. Stated rather
    # than hidden: it is the honest limit of what the schema records, and a
    # separate paid_on column is the fix if the figure ever has to be exact.
    for period, amount in (
        db.query(
            month(Compensation.awarded_on), func.coalesce(func.sum(Compensation.amount_paid), 0)
        )
        .filter(
            Compensation.case_id.in_(case_ids),
            Compensation.awarded_on.isnot(None),
            Compensation.awarded_on >= start,
        )
        .group_by(month(Compensation.awarded_on))
        .all()
    ):
        if period in empty:
            empty[period]["compensation_paid"] = int(amount)

    return TrendSeries(
        points=[TrendPoint(period=p, **empty[p]) for p in periods],
        from_period=periods[0] if periods else "",
        to_period=periods[-1] if periods else "",
    )


@router.get("/forecast", response_model=ForecastResponse)
def dashboard_forecast(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    state_id: int | None = None,
    district_id: int | None = None,
    project_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Projected completion and delay risk, worst first.

    The rules answer "what is wrong now". This answers "what is about to go
    wrong", which is the predictive half the problem statement asks for.

    Every item carries the evidence behind its score. An officer whose case
    has just been flagged is entitled to see which signal did it — a score
    with no explanation is not usable in a process where decisions have to
    be justified.
    """
    try:
        case_ids = resolve_scope(
            db,
            district_id=district_id,
            project_id=project_id,
            base_case_ids=entitled_case_ids(db, user),
            state_id=state_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ForecastResponse(**predict.forecast(db, case_ids, limit=limit))
