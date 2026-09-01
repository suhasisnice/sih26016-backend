from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CaseStatus, Stage, TimelineStatus


class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    project_id: int
    village_id: int
    # district_id is not accepted from the client: it is derived from the
    # village, so the two can never contradict each other.


class CaseStageAdvance(BaseModel):
    to_stage: Stage
    note: str | None = Field(default=None, max_length=300)


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


class CaseUpdate(BaseModel):
    """Editable fields on a case. Deliberately not `stage`.

    The stage moves only through POST /cases/{id}/advance, which validates
    the transition against the Act and writes the stage history. Allowing it
    here would give a second, unvalidated way to move a case and leave the
    timeline with gaps.
    """

    title: str | None = Field(default=None, min_length=3, max_length=200)
    status: CaseStatus | None = None
