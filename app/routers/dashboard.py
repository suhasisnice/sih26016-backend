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
from app.core.enums import (
    AlertSeverity,
    CaseStatus,
    CompensationStatus,
    ObjectionStatus,
    RnRStatus,
    Stage,
)
from app.dependencies import entitled_case_ids, get_current_user, get_db
from app.models import (
    AffectedFamily,
    Alert,
    Case,
    CaseStageHistory,
    Compensation,
    Document,
    District,
    Objection,
    Parcel,
    Project,
    RequiredDocument,
    RnRRecord,
    StatutoryNotice,
    User,
    Village,
)
from app.schemas.dashboard import (
    AlertList,
    AlertOut,
    AttentionCaseOut,
    AttentionList,
    DashboardKpis,
    FieldWorkCaseOut,
    FieldWorkList,
    ForecastResponse,
    StageBreakdownItem,
    TrendPoint,
    TrendSeries,
)
from app.services import sla

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


@router.get("/attention", response_model=AttentionList)
def cases_requiring_attention(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
):
    """One row per case with at least one open finding, worst first — not
    one row per finding the way /alerts is. Every factor the rules engine
    already checks (stalled cases, unanswered objections, possession ahead
    of R&R, unpaid awards, missing documents, deadline breaches, unused
    land) is exactly the priority list a "cases requiring attention" panel
    needs, so this reads the same Alert table POST /admin/run-rules
    populates rather than re-deriving urgency from scratch.
    """
    entitled = entitled_case_ids(db, user)
    if entitled is not None and not entitled:
        return AttentionList(items=[], total=0)

    open_alerts = db.query(Alert).filter(Alert.is_resolved.is_(False))
    if entitled is not None:
        open_alerts = open_alerts.filter(Alert.case_id.in_(entitled))

    alert_counts = dict(
        open_alerts.with_entities(Alert.case_id, func.count(Alert.id)).group_by(Alert.case_id).all()
    )
    case_ids = list(alert_counts.keys())
    if not case_ids:
        return AttentionList(items=[], total=0)

    # The worst alert per case — cheaper to pull every open alert for these
    # cases pre-sorted and keep the first one seen per case in Python than
    # to express "first row per group" as a correlated subquery.
    worst_by_case: dict[int, Alert] = {}
    for alert in (
        db.query(Alert)
        .filter(Alert.case_id.in_(case_ids), Alert.is_resolved.is_(False))
        .order_by(Alert.case_id, SEVERITY_RANK, Alert.id)
        .all()
    ):
        worst_by_case.setdefault(alert.case_id, alert)

    cases = (
        db.query(Case, Project.name, Village.name, District.name)
        .join(Project, Case.project_id == Project.id)
        .join(Village, Case.village_id == Village.id)
        .join(District, Case.district_id == District.id)
        .filter(Case.id.in_(case_ids))
        .all()
    )

    survey_numbers: dict[int, list[str]] = {}
    for case_id, survey_number in (
        db.query(Parcel.case_id, Parcel.survey_number).filter(Parcel.case_id.in_(case_ids)).all()
    ):
        survey_numbers.setdefault(case_id, []).append(survey_number)

    # Whoever most recently moved each case — the closest honest stand-in
    # for "responsible officer" this schema has, since Case carries no
    # assignee column of its own.
    last_mover: dict[int, int | None] = {}
    for case_id, changed_by, _changed_on in (
        db.query(
            CaseStageHistory.case_id,
            CaseStageHistory.changed_by_user_id,
            CaseStageHistory.changed_on,
        )
        .filter(CaseStageHistory.case_id.in_(case_ids))
        .order_by(
            CaseStageHistory.case_id,
            CaseStageHistory.changed_on.desc(),
            CaseStageHistory.id.desc(),
        )
        .all()
    ):
        last_mover.setdefault(case_id, changed_by)
    officer_ids = [v for v in last_mover.values() if v is not None]
    officer_names = (
        {u.id: u.full_name for u in db.query(User).filter(User.id.in_(officer_ids)).all()}
        if officer_ids
        else {}
    )

    family_counts = dict(
        db.query(AffectedFamily.case_id, func.count(AffectedFamily.id))
        .filter(AffectedFamily.case_id.in_(case_ids))
        .group_by(AffectedFamily.case_id)
        .all()
    )
    objection_counts = dict(
        db.query(Objection.case_id, func.count(Objection.id))
        .filter(
            Objection.case_id.in_(case_ids),
            Objection.status.in_([ObjectionStatus.FILED, ObjectionStatus.UNDER_REVIEW]),
        )
        .group_by(Objection.case_id)
        .all()
    )
    compensation_pending = dict(
        db.query(Compensation.case_id, func.count(Compensation.id))
        .filter(
            Compensation.case_id.in_(case_ids),
            Compensation.status != CompensationStatus.PAID,
        )
        .group_by(Compensation.case_id)
        .all()
    )
    rnr_pending = dict(
        db.query(RnRRecord.case_id, func.count(RnRRecord.id))
        .filter(
            RnRRecord.case_id.in_(case_ids),
            RnRRecord.status.in_([RnRStatus.PENDING, RnRStatus.IN_PROGRESS]),
        )
        .group_by(RnRRecord.case_id)
        .all()
    )

    today = date.today()
    sla_table = sla.load_sla(db)
    severity_order = {
        AlertSeverity.CRITICAL: 0,
        AlertSeverity.HIGH: 1,
        AlertSeverity.MEDIUM: 2,
        AlertSeverity.LOW: 3,
    }

    items = []
    for case, project_name, village_name, district_name in cases:
        alert = worst_by_case.get(case.id)
        days_remaining = sla.days_remaining(case.stage_due_on, today)
        items.append(
            AttentionCaseOut(
                case_id=case.id,
                case_number=case.case_number,
                title=case.title,
                project_name=project_name,
                village_name=village_name,
                district_name=district_name,
                survey_numbers=survey_numbers.get(case.id, []),
                stage=case.stage,
                responsible_officer_name=officer_names.get(last_mover.get(case.id)),
                stage_due_on=case.stage_due_on,
                days_remaining=days_remaining,
                timeline_status=sla.timeline_status(case.stage_due_on, case.stage, today, sla_table),
                priority=alert.severity if alert else AlertSeverity.LOW,
                reason=alert.message if alert else "",
                open_alert_count=alert_counts.get(case.id, 0),
                affected_family_count=family_counts.get(case.id, 0),
                open_objection_count=objection_counts.get(case.id, 0),
                compensation_pending_count=compensation_pending.get(case.id, 0),
                rnr_pending_count=rnr_pending.get(case.id, 0),
            )
        )

    # Worst severity first, then soonest deadline — the spec's own stated
    # order (overdue and approaching-deadline outrank the count-based
    # factors), with same-severity cases broken by urgency rather than an
    # arbitrary id order.
    items.sort(
        key=lambda i: (
            severity_order.get(i.priority, 9),
            i.days_remaining if i.days_remaining is not None else 9999,
        )
    )

    return AttentionList(items=items[:limit], total=len(items))


# The three stages a field officer is actually on the ground for. Sections
# 4-9 (social impact assessment) and 12 (land verification) are surveys by
# definition; Section 15 (objection period) is when a filed objection sends
# someone back out to the parcel it names. Declaration onward is paperwork
# and payment, not a site visit.
FIELD_WORK_STAGES = (Stage.SOCIAL_IMPACT_ASSESSMENT, Stage.LAND_VERIFICATION, Stage.OBJECTION_PERIOD)


@router.get("/field-work", response_model=FieldWorkList)
def field_work_queue(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
):
    """What a field officer still has to go out and do.

    Not every case in an on-ground stage — only ones with something a visit
    would actually resolve: no parcel captured yet, a parcel with a GPS
    point but no surveyed boundary (see Parcel.boundary), or a document this
    stage requires but does not have. A case sitting in land_verification
    with its parcels fully surveyed and its documents filed has nothing left
    for a field visit and does not belong on this list.
    """
    entitled = entitled_case_ids(db, user)
    if entitled is not None and not entitled:
        return FieldWorkList(items=[], total=0)

    query = db.query(Case).filter(Case.status == CaseStatus.ACTIVE, Case.stage.in_(FIELD_WORK_STAGES))
    if entitled is not None:
        query = query.filter(Case.id.in_(entitled))
    cases = query.all()
    if not cases:
        return FieldWorkList(items=[], total=0)

    case_ids = [c.id for c in cases]

    parcel_counts: dict[int, int] = dict(
        db.query(Parcel.case_id, func.count(Parcel.id))
        .filter(Parcel.case_id.in_(case_ids))
        .group_by(Parcel.case_id)
        .all()
    )
    missing_boundary_counts: dict[int, int] = dict(
        db.query(Parcel.case_id, func.count(Parcel.id))
        .filter(Parcel.case_id.in_(case_ids), Parcel.boundary.is_(None))
        .group_by(Parcel.case_id)
        .all()
    )

    required_by_stage: dict[Stage, set[DocType]] = {}
    for stage, doc_type in (
        db.query(RequiredDocument.stage, RequiredDocument.doc_type)
        .filter(RequiredDocument.stage.in_(FIELD_WORK_STAGES))
        .all()
    ):
        required_by_stage.setdefault(stage, set()).add(doc_type)

    present_by_case: dict[int, set[DocType]] = {}
    for case_id, doc_type in (
        db.query(Document.case_id, Document.doc_type)
        .filter(Document.case_id.in_(case_ids), Document.is_current.is_(True))
        .all()
    ):
        present_by_case.setdefault(case_id, set()).add(doc_type)

    family_counts = dict(
        db.query(AffectedFamily.case_id, func.count(AffectedFamily.id))
        .filter(AffectedFamily.case_id.in_(case_ids))
        .group_by(AffectedFamily.case_id)
        .all()
    )

    projects = {p.id: p.name for p in db.query(Project).filter(Project.id.in_({c.project_id for c in cases}))}
    villages = {v.id: v.name for v in db.query(Village).filter(Village.id.in_({c.village_id for c in cases}))}
    districts = {
        d.id: d.name for d in db.query(District).filter(District.id.in_({c.district_id for c in cases}))
    }

    today = date.today()
    sla_table = sla.load_sla(db)

    items = []
    for case in cases:
        parcel_count = parcel_counts.get(case.id, 0)
        parcels_missing_boundary = missing_boundary_counts.get(case.id, 0)
        missing_docs = sorted(
            required_by_stage.get(case.stage, set()) - present_by_case.get(case.id, set()),
            key=lambda d: d.value,
        )
        no_parcels_yet = parcel_count == 0 and case.stage != Stage.SOCIAL_IMPACT_ASSESSMENT
        if parcels_missing_boundary == 0 and not missing_docs and not no_parcels_yet:
            continue

        items.append(
            FieldWorkCaseOut(
                case_id=case.id,
                case_number=case.case_number,
                title=case.title,
                project_name=projects.get(case.project_id, ""),
                village_name=villages.get(case.village_id, ""),
                district_name=districts.get(case.district_id, ""),
                stage=case.stage,
                stage_due_on=case.stage_due_on,
                days_remaining=sla.days_remaining(case.stage_due_on, today),
                timeline_status=sla.timeline_status(case.stage_due_on, case.stage, today, sla_table),
                parcel_count=parcel_count,
                parcels_missing_boundary=parcels_missing_boundary,
                missing_document_types=missing_docs,
                affected_family_count=family_counts.get(case.id, 0),
            )
        )

    # Overdue first, then soonest deadline — same ordering rule as
    # /attention, since a field officer's queue is triaged the same way.
    items.sort(key=lambda i: (i.days_remaining if i.days_remaining is not None else 9999,))

    return FieldWorkList(items=items[:limit], total=len(items))


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
