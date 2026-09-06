"""One field survey job, from assignment through review. See SurveyTask's
docstring in app.models.tables for why this is its own entity rather than
just another parcel field.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import SurveyTaskStatus


class LatLng(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class SurveyTaskCreate(BaseModel):
    case_id: int
    parcel_id: int | None = None
    # Defaults to the caller on the router side — a field officer self-
    # starting a survey never sends this at all.
    assigned_to_user_id: int | None = None
    due_on: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class SurveyTaskSaveRequest(BaseModel):
    """Every field optional so the entry portal can autosave whatever the
    officer has filled in so far without restating the rest."""

    measured_area_ha: float | None = Field(default=None, gt=0, le=10_000)
    # At least 3 distinct corners to form a real polygon; the router closes
    # the ring itself rather than asking the client to repeat the first
    # point.
    boundary_points: list[LatLng] | None = None
    location: LatLng | None = None
    remarks: str | None = Field(default=None, max_length=4000)


class SurveyReviewRequest(BaseModel):
    """Optional to approve, required to return — enforced in the router the
    same way documents.py's DocumentVerifyRequest handles a correction
    request versus a plain verify."""

    review_note: str | None = Field(default=None, max_length=500)


class SurveyPhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    latitude: float | None
    longitude: float | None
    caption: str | None
    uploaded_at: datetime


class SurveyTaskOut(BaseModel):
    id: int
    case_id: int
    case_number: str
    parcel_id: int | None
    parcel_survey_number: str | None
    project_name: str
    village_name: str
    assigned_to_user_id: int
    assigned_to_name: str
    assigned_by_user_id: int | None
    assigned_by_name: str | None
    status: SurveyTaskStatus
    due_on: date | None
    notes: str | None
    created_at: datetime
    started_at: datetime | None
    measured_area_ha: float | None
    # Corner count rather than the raw geometry — the detail page needs "4
    # corners recorded" to render its own map, not a WKT string to parse.
    boundary_point_count: int
    has_location: bool
    remarks: str | None
    submitted_at: datetime | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    photos: list[SurveyPhotoOut]


class SurveyTaskList(BaseModel):
    items: list[SurveyTaskOut]
    total: int


class AssignableOfficerOut(BaseModel):
    id: int
    full_name: str
    username: str
