"""Pushing a possession to the state land-record portal — see
app.models.tables.MutationRequest for why this is the opposite direction
from the Sec. 12 reconciliation lookup."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.core.enums import MutationStatus


class MutationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parcel_id: int
    case_id: int
    ulpin: str | None
    adapter: str
    sent_on: date
    external_ref: str | None
    status: MutationStatus
    response_payload: dict
    requested_by_user_id: int | None
    created_at: datetime


class MutationRequestList(BaseModel):
    items: list[MutationRequestOut]
    total: int
