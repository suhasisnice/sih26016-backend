"""Reference data for the pickers and filter dropdowns Frontend builds.

Without these the create-case form has nothing to populate its village
selector with, and the dashboard's district filter has no list of
districts to offer.
"""

from pydantic import BaseModel, ConfigDict


class DistrictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    state: str
    code: str


class VillageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    district_id: int
    district_name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    requiring_body: str
    district_id: int
    district_name: str
    case_count: int
