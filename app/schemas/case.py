from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CaseStatus, Stage, TimelineStatus


class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    project_id: int
    village_id: int
    # district_id is not accepted from the client: it is derived from the
    # village, so the two can never contradict each other.
    # Sec. 2(2): 70 for a PPP project, 80 for a private company, null for a
    # government undertaking that needs no consent at all.
    consent_threshold_pct: float | None = Field(default=None, ge=0, le=100)


class CaseStageAdvance(BaseModel):
    to_stage: Stage
    # Optional moving forward; the router requires it when to_stage is
    # behind the case's current stage — see cases.py's advance_stage.
    note: str | None = Field(default=None, max_length=300)


class CaseHoldRequest(BaseModel):
    """The closest thing this system has to "reject": RFCTLARR cases don't
    have a legal rejection, only a stage reached or not, so holding a case
    sets CaseStatus.STALLED with a mandatory reason rather than inventing a
    terminal state the Act does not recognise. See POST /cases/{id}/hold."""

    note: str = Field(min_length=3, max_length=300)


class CaseResumeRequest(BaseModel):
    note: str = Field(min_length=3, max_length=300)


class CaseStageHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_stage: Stage | None
    to_stage: Stage
    changed_on: date
    changed_by_user_id: int | None
    note: str | None


class CaseListItem(BaseModel):
    """The case table row. Kept lean on purpose — the list is the busiest
    endpoint in the app and Frontend paginates it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    case_number: str
    title: str
    stage: Stage
    status: CaseStatus
    district_id: int
    district_name: str
    village_name: str
    project_name: str
    stage_changed_at: date
    days_in_stage: int
    parcel_count: int
    total_area_ha: float
    # Timeline position, on the row rather than behind a second request:
    # the case table is where an officer decides what to work on next, and
    # "which of these is late" is the question they are asking.
    stage_due_on: date | None = None
    days_remaining: int | None = None
    timeline_status: TimelineStatus = TimelineStatus.ON_TIME


class PaginatedCases(BaseModel):
    items: list[CaseListItem]
    total: int
    limit: int
    offset: int


class CaseDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_number: str
    title: str
    stage: Stage
    status: CaseStatus
    project_id: int
    project_name: str
    district_id: int
    district_name: str
    village_id: int
    village_name: str
    stage_changed_at: date
    created_at: date
    days_in_stage: int
    parcel_count: int
    total_area_ha: float
    allowed_next_stages: list[Stage]
    stage_history: list[CaseStageHistoryOut]
    # Timeline against the stage's own allowance, not a flat threshold.
    stage_due_on: date | None = None
    days_remaining: int | None = None
    timeline_status: TimelineStatus = TimelineStatus.ON_TIME
    standard_days: int | None = None
    statutory_days: int | None = None
    sla_basis: str | None = None
    # Set when the case came from a sanctioned proposal; null for cases
    # opened directly, which is how every case worked before the proposal
    # workflow existed.
    proposal_id: int | None = None
    proposal_number: str | None = None
    # Sec. 2(2) consent. Threshold is null when the project needs none.
    # `obtained_pct` is never stored — it is counted live off
    # affected_families.consent_given, same discipline as every other
    # progress figure in this system (Law 1: no endpoint writes an
    # aggregate).
    consent_threshold_pct: float | None = None
    consent_family_count: int = 0
    consent_given_count: int = 0
    consent_obtained_pct: float | None = None


class CaseUpdate(BaseModel):
    """Editable fields on a case. Deliberately not `stage`.

    The stage moves only through POST /cases/{id}/advance, which validates
    the transition against the Act and writes the stage history. Allowing it
    here would give a second, unvalidated way to move a case and leave the
    timeline with gaps.
    """

    title: str | None = Field(default=None, min_length=3, max_length=200)
    status: CaseStatus | None = None
    consent_threshold_pct: float | None = Field(default=None, ge=0, le=100)
