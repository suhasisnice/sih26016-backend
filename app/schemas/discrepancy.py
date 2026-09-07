from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import DiscrepancyStatus, DiscrepancyType


class DiscrepancyCreate(BaseModel):
    survey_task_id: int
    discrepancy_type: DiscrepancyType
    description: str = Field(min_length=10, max_length=2000)


class DiscrepancyRespond(BaseModel):
    response: str = Field(min_length=5, max_length=2000)


class DiscrepancyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    survey_task_id: int
    case_id: int
    case_number: str
    discrepancy_type: DiscrepancyType
    description: str
    status: DiscrepancyStatus
    filed_by_name: str
    filed_on: date
    response: str | None
    responded_by_name: str | None
    responded_on: date | None


class DiscrepancyList(BaseModel):
    items: list[DiscrepancyOut]
    total: int
    open_count: int
