"""Parcel shapes, including real GeoJSON for the map.

The bbox endpoint returns a genuine GeoJSON FeatureCollection rather than a
custom shape, so Frontend can hand the response straight to a map component
without translating it first.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import ParcelStatus
from app.schemas.provenance import ProvenanceOut

ULPIN_RE = re.compile(r"^[A-Z0-9]{14}$")


class ParcelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    survey_number: str
    ulpin: str | None = None
    area_ha: float
    status: ParcelStatus
    owner_id: int
    owner_name: str
    longitude: float
    latitude: float
    provenance: ProvenanceOut


class PointGeometry(BaseModel):
    """A parcel with a GPS fix and no survey attached."""

    type: Literal["Point"] = "Point"
    coordinates: list[float]  # [longitude, latitude], per the GeoJSON spec


class PolygonGeometry(BaseModel):
    """A surveyed parcel outline.

    Nesting is the GeoJSON spec's, not ours: a Polygon is a list of linear
    rings, the first being the exterior and any others holes, and each ring
    is a list of [lon, lat] pairs whose last point repeats its first. Parcels
    have no holes, so the outer list always has exactly one entry — but the
    shape has to be the spec's, because the whole point of returning real
    GeoJSON is that a map component consumes it without translation.
    """

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[list[float]]]


# Discriminated on `type`, so a client reading the OpenAPI schema is told
# which of the two it is getting rather than having to sniff the payload.
ParcelGeometry = Annotated[
    PointGeometry | PolygonGeometry,
    Field(discriminator="type"),
]


class ParcelProperties(BaseModel):
    id: int
    case_id: int
    case_number: str
    survey_number: str
    ulpin: str | None = None
    area_ha: float
    status: ParcelStatus
    owner_name: str
    # The centre, always, even when `geometry` is the polygon. A map needs a
    # point to anchor a label, to fly to on a search hit, and to draw at zoom
    # levels where a 0.4 ha plot is smaller than one screen pixel. Deriving
    # it in the browser from the ring would mean every client reimplementing
    # a centroid, and getting it slightly differently.
    longitude: float
    latitude: float
    # Whether `geometry` above is a surveyed outline or just the fix. The map
    # says so in the sidebar; a reviewer is entitled to know which they are
    # looking at.
    has_boundary: bool = False
    provenance: ProvenanceOut


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
    # The 14-digit ULPIN, when the field officer has one to hand — a plot
    # surveyed under DILRMP carries one before it is ever acquired. Optional:
    # plenty of real parcels still don't have one issued.
    ulpin: str | None = Field(default=None, max_length=14)
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

    @field_validator("ulpin")
    @classmethod
    def _valid_ulpin(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        value = value.strip().upper()
        if not ULPIN_RE.match(value):
            raise ValueError("ULPIN must be 14 alphanumeric characters")
        return value


class ParcelUpdate(BaseModel):
    """Correct a parcel, or move it along.

    Ownership is not editable here. Reassigning a parcel to a different
    person changes who is owed compensation for it, which is a record
    correction with its own consequences, not a field edit.
    """

    survey_number: str | None = Field(default=None, min_length=1, max_length=20)
    ulpin: str | None = Field(default=None, max_length=14)
    area_ha: float | None = Field(default=None, gt=0, le=10_000)
    status: ParcelStatus | None = None
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)

    @field_validator("ulpin")
    @classmethod
    def _valid_ulpin(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        value = value.strip().upper()
        if not ULPIN_RE.match(value):
            raise ValueError("ULPIN must be 14 alphanumeric characters")
        return value
