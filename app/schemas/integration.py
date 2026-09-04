"""The published shape of the external land-record integration."""

from pydantic import BaseModel, Field


class ProviderOut(BaseModel):
    key: str
    label: str
    authority: str
    # The field that keeps the demo honest. False for the mock, and the UI
    # badges it on every screen that shows upstream data.
    is_live: bool = Field(
        description="False when this provider is a simulation rather than a real portal."
    )
    covers_states: list[str] = []


class ProviderList(BaseModel):
    items: list[ProviderOut]
    # Which one this deployment is actually wired to.
    configured: str


class UpstreamRecordOut(BaseModel):
    village_lgd: str
    survey_number: str
    owner_name: str
    area_ha: float
    land_classification: str | None = None
    encumbrance: str | None = None
    mutation_pending: bool = False
    record_as_of: str | None = None
    provider: str
    is_live: bool


class ReconciliationItem(BaseModel):
    parcel_id: int
    survey_number: str
    status: str = Field(
        description=(
            "matched | area_mismatch | owner_mismatch | not_found_upstream | unavailable"
        )
    )
    local_owner_name: str
    local_area_ha: float
    upstream_owner_name: str | None = None
    upstream_area_ha: float | None = None
    land_classification: str | None = None
    encumbrance: str | None = None
    mutation_pending: bool = False
    record_as_of: str | None = None
    note: str | None = None
    needs_attention: bool


class ReconciliationReport(BaseModel):
    case_id: int
    village_lgd: str | None = None
    provider: str
    provider_label: str
    is_live: bool
    parcels_checked: int
    needs_attention: int
    by_status: dict[str, int]
    items: list[ReconciliationItem]
