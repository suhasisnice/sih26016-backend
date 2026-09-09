from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import GrievanceCategory, GrievanceContactMethod, GrievanceStatus, Role


class GrievanceRespond(BaseModel):
    status: GrievanceStatus
    response: str = Field(min_length=5, max_length=2000)


class GrievanceStatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: GrievanceStatus | None
    to_status: GrievanceStatus
    changed_on: date
    changed_by_name: str | None
    note: str | None


class GrievanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    grievance_number: str
    case_id: int
    case_number: str
    person_id: int
    person_name: str
    category: GrievanceCategory
    # Display only — see app.services.grievances.GRIEVANCE_CATEGORY_ROLE's
    # own docstring on why this is a routing hint, not an access boundary.
    assigned_role: Role
    subject: str
    description: str
    status: GrievanceStatus
    preferred_contact_method: GrievanceContactMethod
    filed_on: date
    response: str | None
    responded_on: date | None
    has_attachment: bool
    attachment_filename: str | None
    days_open: int | None


class GrievanceDetail(GrievanceOut):
    history: list[GrievanceStatusHistoryOut]


class GrievanceList(BaseModel):
    items: list[GrievanceOut]
    total: int
    open_count: int
