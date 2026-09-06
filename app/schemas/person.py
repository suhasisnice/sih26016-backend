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

from app.core.enums import BenefitCategory, BenefitDeliveryStatus, CompensationStatus, RnRStatus
from app.schemas.provenance import ProvenanceOut


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


class RnrBenefitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rnr_record_id: int
    category: BenefitCategory
    description: str | None
    responsible_department: str | None
    approved_on: date | None
    expected_on: date | None
    delivery_status: BenefitDeliveryStatus
    evidence_document_id: int | None
    note: str | None
    updated_on: date


class RnrBenefitCreate(BaseModel):
    category: BenefitCategory
    description: str | None = Field(default=None, max_length=200)
    responsible_department: str | None = Field(default=None, max_length=120)
    approved_on: date | None = None
    expected_on: date | None = None


class RnrBenefitUpdate(BaseModel):
    """Every field optional so a benefit can be nudged along one attribute
    at a time — e.g. marking it delivered without restating who's
    responsible for it."""

    description: str | None = Field(default=None, max_length=200)
    responsible_department: str | None = Field(default=None, max_length=120)
    approved_on: date | None = None
    expected_on: date | None = None
    delivery_status: BenefitDeliveryStatus | None = None
    evidence_document_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


class AffectedPersonOut(BaseModel):
    person_id: int
    name: str
    village_name: str
    has_land_title: bool
    is_landowner: bool
    # Losing a dwelling, which is not the same as losing land. Reported
    # separately because the Act and the problem statement both
    # distinguish affected from displaced.
    is_displaced: bool = False
    # Sec. 2(2) consent, per family. Meaningless when the case's project
    # needs no consent (Case.consent_threshold_pct is null) — carried here
    # regardless so the household list reads the same whether or not this
    # particular project gates on it.
    consent_given: bool = False
    parcel_count: int
    total_area_ha: float
    # Null when this household owns no acquired land. Not zero — zero would
    # read as "awarded nothing", which is a different statement.
    compensation: CompensationOut | None = None
    rnr: RnROut | None = None
    provenance: ProvenanceOut


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
    # Whether this household loses its dwelling. Independent of both flags
    # above: a landowner farming an outlying plot is affected but not
    # displaced, and a labourer whose house stands on the acquired parcel is
    # displaced while owning nothing.
    is_displaced: bool = False
    consent_given: bool = False
    rnr_entitlement: str | None = Field(default=None, max_length=200)


class AffectedPersonUpdate(BaseModel):
    """Correct a household's classification on a case.

    Displacement in particular is established during the Social Impact
    Assessment and routinely corrected afterwards, so it has to be editable
    — a survey finding that a house sits inside the notified boundary is
    exactly the kind of thing that arrives late.
    """

    is_landowner: bool | None = None
    is_displaced: bool | None = None
    consent_given: bool | None = None
