from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ObjectionStatus


class ObjectionCreate(BaseModel):
    case_id: int
    grounds: str = Field(min_length=10, max_length=2000)
    # person_id is not accepted from the client. A landowner files as
    # themselves, and an officer filing on someone's behalf must say who
    # via on_behalf_of_person_id, which is checked against the case.
    on_behalf_of_person_id: int | None = None


class ObjectionRespond(BaseModel):
    status: ObjectionStatus
    response: str = Field(min_length=5, max_length=2000)


class ObjectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    case_number: str
    person_id: int
    person_name: str
    grounds: str
    status: ObjectionStatus
    filed_on: date
    response: str | None
    responded_on: date | None
    days_open: int | None
    is_overdue: bool


class ObjectionList(BaseModel):
    items: list[ObjectionOut]
    total: int
    open_count: int
    overdue_count: int
