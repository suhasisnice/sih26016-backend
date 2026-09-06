"""Dashboard contract — the KPI tiles, the alerts panel, the forecast.

Every field here is named exactly as the problem statement names it, so
Frontend's tiles map one-to-one onto the response without translation.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AlertSeverity, DocType, RiskBand, Stage, TimelineStatus


class KpiScope(BaseModel):
    """What this set of numbers describes. Echoed back so a screen can say
    'Bengaluru Rural, 22 cases' instead of just showing bare totals."""

    state_id: int | None = None
    district_id: int | None = None
    project_id: int | None = None
    case_count: int


class DashboardKpis(BaseModel):
    scope: KpiScope

    # 1. Area
    area_notified_ha: float
    area_acquired_ha: float

    # 2. Compensation, in whole rupees
    compensation_awarded_total: int
    compensation_paid_total: int
    compensation_pending_total: int

    # 3. Affected AND displaced families — two figures, because the Act
    #    distinguishes them and neither is derivable from the other.
    affected_families_count: int
    affected_families_landowner_count: int
    affected_families_landless_count: int
    displaced_families_count: int
    displaced_families_landless_count: int

    # 4. R&R — deliberately separate from compensation above. Never sum
    #    these with the compensation figures.
    rnr_entitled_count: int
    rnr_pending_count: int
    rnr_in_progress_count: int
    rnr_completed_count: int
    rnr_disputed_count: int

    # 5. Possession, counted in parcels
    possession_taken_count: int
    possession_pending_count: int

    # 6. Timeline adherence. `timeline_adherence_pct` is None rather than
    #    100 when nothing has a deadline on file — an empty denominator is
    #    unknown, not perfect, and a spurious 100% is the one number on this
    #    screen nobody would question.
    timeline_on_time_count: int
    timeline_at_risk_count: int
    timeline_breached_count: int
    timeline_untracked_count: int
    timeline_adherence_pct: float | None

    # 7. Published instruments. These count statutory_notices rows, so they
    #    only ever rise — unlike the stage-inferred figures they replaced,
    #    which fell as cases progressed past the stage being counted.
    notifications_issued_count: int
    declarations_issued_count: int
    awards_declared_count: int
    possession_notices_count: int
    awards_declared_amount: int


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    case_number: str
    district_id: int
    stage: Stage
    rule: str
    severity: AlertSeverity
    message: str
    detected_on: date
    details: dict = Field(default_factory=dict)
    is_resolved: bool


class AlertList(BaseModel):
    items: list[AlertOut]
    total: int
    by_severity: dict[str, int]
    by_rule: dict[str, int]


class StageBreakdownItem(BaseModel):
    stage: Stage
    case_count: int
    # Included so the stage chart can shade bars by how much of each stage is
    # behind schedule, rather than needing a second request per stage.
    breached_count: int = 0


class TrendPoint(BaseModel):
    """One month of cumulative progress.

    Cumulative, not per-period: the question a reviewing officer asks is
    "how much has been acquired by now", and a per-month bar chart of
    completions answers a different one.
    """

    period: str  # YYYY-MM
    cases_opened: int
    cases_closed: int
    stage_transitions: int
    notices_issued: int
    compensation_paid: int
    area_acquired_ha: float


class TrendSeries(BaseModel):
    points: list[TrendPoint]
    from_period: str
    to_period: str


class ForecastItem(BaseModel):
    case_id: int
    case_number: str
    stage: str
    days_in_stage: int
    projected_completion: date
    pessimistic_completion: date
    projected_days_remaining: int
    risk_score: float
    risk_band: RiskBand
    # Named in plain words, because "elapsed_fraction=0.82" is not something
    # an officer can act on.
    primary_driver: str
    confidence: str
    evidence: dict = Field(default_factory=dict)


class ForecastResponse(BaseModel):
    as_of: date
    cases_forecast: int
    summary: dict[str, int]
    items: list[ForecastItem]
    # The observed stage durations the projection was built from. Published
    # so the forecast is inspectable rather than taken on faith.
    stage_model: dict[str, dict]


class CaseTimelineOut(BaseModel):
    """Timeline position for one case, on its detail page."""

    stage_due_on: date | None
    days_remaining: int | None
    timeline_status: TimelineStatus
    standard_days: int | None
    statutory_days: int | None
    basis: str | None


class AttentionCaseOut(BaseModel):
    """One row of "Cases Requiring Attention" — a case, not a finding.
    Several rules can fire on the same case (stalled AND an unanswered
    objection); this collapses them to the worst one so a Collector sees
    one row per case to act on, not one row per thing wrong with it.
    """

    case_id: int
    case_number: str
    title: str
    project_name: str
    village_name: str
    district_name: str
    survey_numbers: list[str]
    stage: Stage
    # Whoever most recently moved this case, from its own stage history —
    # there is no separate "assigned officer" column on Case, so this is
    # the closest honest answer to "who is this case sitting with".
    responsible_officer_name: str | None
    stage_due_on: date | None
    days_remaining: int | None
    timeline_status: TimelineStatus
    priority: AlertSeverity
    reason: str
    open_alert_count: int
    affected_family_count: int
    open_objection_count: int
    compensation_pending_count: int
    rnr_pending_count: int


class AttentionList(BaseModel):
    items: list[AttentionCaseOut]
    total: int


class FieldWorkCaseOut(BaseModel):
    """One row of the Field Officer's work queue — a case in one of the
    three on-ground stages (social impact assessment, land verification,
    objection period) that still has something a field visit would
    resolve: no parcels captured yet, a parcel with a GPS point but no
    surveyed boundary, or a document this stage requires but does not
    have. A case in these stages with none of that outstanding does not
    appear — the queue is what to go do, not every case currently open.
    """

    case_id: int
    case_number: str
    title: str
    project_name: str
    village_name: str
    district_name: str
    stage: Stage
    stage_due_on: date | None
    days_remaining: int | None
    timeline_status: TimelineStatus
    parcel_count: int
    parcels_missing_boundary: int
    missing_document_types: list[DocType]
    affected_family_count: int


class FieldWorkList(BaseModel):
    items: list[FieldWorkCaseOut]
    total: int


class RunRulesResult(BaseModel):
    cases_evaluated: int
    alerts_generated: int
    by_rule: dict[str, int]
    by_severity: dict[str, int]
    as_of: date
    # Fan-out results, so one call reports both what was found and who was
    # told about it.
    notifications_created: int = 0
    notification_recipients: int = 0
