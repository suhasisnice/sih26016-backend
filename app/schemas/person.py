"""Affected people, with compensation and R&R reported side by side but
never merged.

This is where the distinction becomes visible on screen: `compensation` is
null for a tenant farmer who owns no land, while `rnr` is still populated,
because resettlement support is owed to displaced households regardless of
title. A single combined "status" column here would misrepresent exactly
the people the Act exists to protect.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

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


class CompensationUpdate(BaseModel):
    """Compensation is edited on its own, never alongside R&R.

    Every field is optional so a payment can be recorded without restating
    the award. The route rejects a paid amount above the awarded amount:
    that combination is not a state the Act allows, and letting it through
    would quietly corrupt the dashboard's awarded-vs-paid figure.
    """

    amount_awarded: int | None = Field(default=None, ge=0)
    amount_paid: int | None = Field(default=None, ge=0)
    status: CompensationStatus | None = None
    awarded_on: date | None = None


class RnRUpdate(BaseModel):
    """Rehabilitation and resettlement, edited separately from compensation.

    A household with no land title has no compensation record at all and
    still has this one. Merging the two updates into a single endpoint
    would make that case unrepresentable.
    """

    status: RnRStatus | None = None
    entitlement: str | None = Field(default=None, max_length=200)


class AffectedPersonCreate(BaseModel):
    """Add a household to a case.

    `has_land_title` and `is_landowner` are separate on purpose: title is a
    property of the person, while being a landowner *in this case* is a
    property of the relationship. A titled owner elsewhere can be a
    landless affected party here.
    """

    case_id: int
    name: str = Field(min_length=2, max_length=120)
    village_id: int
    phone: str | None = Field(default=None, max_length=15)
    has_land_title: bool = True
    is_landowner: bool = False
    rnr_entitlement: str | None = Field(default=None, max_length=200)
