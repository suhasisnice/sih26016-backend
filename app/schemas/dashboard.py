"""Dashboard contract — the five KPI tiles and the alerts panel.

Every field here is named exactly as the problem statement names it, so
Frontend's tiles map one-to-one onto the response without translation.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AlertSeverity, Stage


class KpiScope(BaseModel):
    """What this set of numbers describes. Echoed back so a screen can say
    'Bengaluru Rural, 22 cases' instead of just showing bare totals."""

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

    # 3. Affected families — broader than landowners; the split is
    #    returned so the difference is visible on screen.
    affected_families_count: int
    affected_families_landowner_count: int
    affected_families_landless_count: int

    # 4. R&R — deliberately separate from compensation above. Never sum
    #    these with the compensation figures.
    rnr_entitled_count: int
    rnr_in_progress_count: int
    rnr_completed_count: int
    rnr_disputed_count: int

    # 5. Possession, counted in parcels
    possession_taken_count: int
    possession_pending_count: int


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


class RunRulesResult(BaseModel):
    cases_evaluated: int
    alerts_generated: int
    by_rule: dict[str, int]
    by_severity: dict[str, int]
    as_of: date
