"""Parcel shapes, including real GeoJSON for the map.

The bbox endpoint returns a genuine GeoJSON FeatureCollection rather than a
custom shape, so Frontend can hand the response straight to a map component
without translating it first.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ParcelStatus


class ParcelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    survey_number: str
    area_ha: float
    status: ParcelStatus
    owner_id: int
    owner_name: str
    longitude: float
    latitude: float


class ParcelGeometry(BaseModel):
    type: str = "Point"
    coordinates: list[float]  # [longitude, latitude], per the GeoJSON spec


class ParcelProperties(BaseModel):
    id: int
    case_id: int
    case_number: str
    survey_number: str
    area_ha: float
    status: ParcelStatus
    owner_name: str


class ParcelFeature(BaseModel):
    type: str = "Feature"
    geometry: ParcelGeometry
    properties: ParcelProperties


class ParcelFeatureCollection(BaseModel):
    type: str = "FeatureCollection"
    features: list[ParcelFeature]
    # Frontend needs to know when a bbox response was capped, so it can say
    # "zoom in for more" rather than silently drawing a partial map.
    truncated: bool = Field(
        default=False,
        description="True when more parcels matched than the limit returned.",
    )
