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


class ParcelCreate(BaseModel):
    """Register a parcel, geo-tagged where it stands.

    This is the field-collection path the system did not have: parcels
    existed only in the seed, so a field officer had no way to record one.
    Coordinates come from the device, which is why they are validated to
    real WGS84 bounds here rather than trusted — a phone with no fix
    reports (0, 0), which is in the Atlantic, and one bad reading places a
    Karnataka parcel off the coast of Ghana on the national map.
    """

    case_id: int
    survey_number: str = Field(min_length=1, max_length=20)
    area_ha: float = Field(gt=0, le=10_000, description="Hectares; must be positive")
    owner_id: int
    status: ParcelStatus = ParcelStatus.NOTIFIED
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    # Metres of GPS uncertainty reported by the device, kept for the record.
    # A reading taken under tree cover is worth less than one in an open
    # field, and a verification trail that cannot say which is which is not
    # much of a trail.
    gps_accuracy_m: float | None = Field(default=None, ge=0, le=10_000)


class ParcelUpdate(BaseModel):
    """Correct a parcel, or move it along.

    Ownership is not editable here. Reassigning a parcel to a different
    person changes who is owed compensation for it, which is a record
    correction with its own consequences, not a field edit.
    """

    survey_number: str | None = Field(default=None, min_length=1, max_length=20)
    area_ha: float | None = Field(default=None, gt=0, le=10_000)
    status: ParcelStatus | None = None
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
