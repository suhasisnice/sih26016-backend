"""Affected people, with compensation and R&R reported side by side but
never merged.

This is where the distinction becomes visible on screen: `compensation` is
null for a tenant farmer who owns no land, while `rnr` is still populated,
because resettlement support is owed to displaced households regardless of
title. A single combined "status" column here would misrepresent exactly
the people the Act exists to protect.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.core.enums import CompensationStatus, RnRStatus


class CompensationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount_awarded: int
    amount_paid: int
    amount_pending: int
    status: CompensationStatus
    awarded_on: date | None


class RnROut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: RnRStatus
    entitlement: str | None
    updated_on: date


class AffectedPersonOut(BaseModel):
    person_id: int
    name: str
    village_name: str
    has_land_title: bool
    is_landowner: bool
    parcel_count: int
    total_area_ha: float
    # Null when this household owns no acquired land. Not zero — zero would
    # read as "awarded nothing", which is a different statement.
    compensation: CompensationOut | None = None
    rnr: RnROut | None = None


class AffectedPersonList(BaseModel):
    items: list[AffectedPersonOut]
    total: int
    landowner_count: int
    landless_count: int
