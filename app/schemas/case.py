from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CaseStatus, Stage


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
