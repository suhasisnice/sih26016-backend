"""Reference data for the pickers and filter dropdowns Frontend builds.

Without these the create-case form has nothing to populate its village
selector with, and the dashboard's district filter has no list of
districts to offer.
"""

from pydantic import BaseModel, ConfigDict


class StateOut(BaseModel):
    """A State or Union Territory.

    `lgd_code` is the Local Government Directory identifier, which is what
    every other Indian government system joins on. Publishing it is what
    makes an integration possible without a name-matching heuristic.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    lgd_code: str | None = None
    is_union_territory: bool = False
    district_count: int = 0
    case_count: int = 0


class DistrictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    # Kept as the state NAME rather than swapped for an id, so existing
    # frontend code that reads `district.state` keeps working. The id is
    # published alongside for anything that needs to filter by it.
    state: str
    state_id: int
    code: str
    lgd_code: str | None = None


class VillageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    district_id: int
    district_name: str
    lgd_code: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    requiring_body: str
    district_id: int
    district_name: str
    case_count: int
