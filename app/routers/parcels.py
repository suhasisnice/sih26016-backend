from fastapi import APIRouter, Depends, HTTPException, Query, status
from geoalchemy2.functions import ST_MakeEnvelope, ST_X, ST_Y
from sqlalchemy.orm import Session

from app.core.enums import ParcelStatus
from app.dependencies import get_current_user, get_db, scope_cases_to_user
from app.models import Case, Parcel, Person, User
from app.schemas import (
    ParcelFeature,
    ParcelFeatureCollection,
    ParcelGeometry,
    ParcelOut,
)
from app.schemas.geo import ParcelProperties

router = APIRouter(prefix="/parcels", tags=["parcels"])

# A map viewport can cover thousands of parcels. Capping the response keeps
# one careless zoom-out from pulling the whole country into the browser;
# the payload says when it was capped so the map can ask the user to zoom.
BBOX_FEATURE_LIMIT = 1000


def _visible_case_ids(db: Session, user: User):
    return scope_cases_to_user(db.query(Case.id), user).subquery().select()


@router.get("/bbox", response_model=ParcelFeatureCollection)
def parcels_in_bbox(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    min_lon: float = Query(ge=-180, le=180),
    min_lat: float = Query(ge=-90, le=90),
    max_lon: float = Query(ge=-180, le=180),
    max_lat: float = Query(ge=-90, le=90),
    parcel_status: ParcelStatus | None = None,
):
    """Parcels inside the map's current viewport, as GeoJSON.

    The spatial filter runs in PostGIS against the GiST index, so the
    database returns only what is on screen instead of the API loading
    every parcel and discarding most of them.
    """
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_lon/min_lat must be smaller than max_lon/max_lat",
        )

    envelope = ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
    query = (
        db.query(Parcel, Case.case_number, Person.name, ST_X(Parcel.geom), ST_Y(Parcel.geom))
        .join(Case, Parcel.case_id == Case.id)
        .join(Person, Parcel.owner_id == Person.id)
        .filter(Parcel.geom.ST_Intersects(envelope))
        .filter(Parcel.case_id.in_(_visible_case_ids(db, user)))
    )
    if parcel_status is not None:
        query = query.filter(Parcel.status == parcel_status)

    # Fetch one extra to detect truncation without a second count query.
    rows = query.order_by(Parcel.id).limit(BBOX_FEATURE_LIMIT + 1).all()
    truncated = len(rows) > BBOX_FEATURE_LIMIT
    rows = rows[:BBOX_FEATURE_LIMIT]

    features = [
        ParcelFeature(
            geometry=ParcelGeometry(coordinates=[float(lon), float(lat)]),
            properties=ParcelProperties(
                id=parcel.id,
                case_id=parcel.case_id,
                case_number=case_number,
                survey_number=parcel.survey_number,
                area_ha=parcel.area_ha,
                status=parcel.status,
                owner_name=owner_name,
            ),
        )
        for parcel, case_number, owner_name, lon, lat in rows
    ]
    return ParcelFeatureCollection(features=features, truncated=truncated)


@router.get("/search", response_model=list[ParcelOut])
def search_parcels(
    survey_number: str = Query(min_length=1, max_length=20, description="Full or partial survey number"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Find parcels by survey number so the map can jump to one.

    Matched with a bound ilike parameter — the wildcards are ours, the
    value stays parameterised, so % or _ in the input cannot restructure
    the query.
    """
    rows = (
        db.query(Parcel, Person.name, ST_X(Parcel.geom), ST_Y(Parcel.geom))
        .join(Person, Parcel.owner_id == Person.id)
        .filter(Parcel.survey_number.ilike(f"%{survey_number}%"))
        .filter(Parcel.case_id.in_(_visible_case_ids(db, user)))
        .order_by(Parcel.survey_number)
        .limit(limit)
        .all()
    )
    return [
        ParcelOut(
            id=parcel.id,
            case_id=parcel.case_id,
            survey_number=parcel.survey_number,
            area_ha=parcel.area_ha,
            status=parcel.status,
            owner_id=parcel.owner_id,
            owner_name=owner_name,
            longitude=float(lon),
            latitude=float(lat),
        )
        for parcel, owner_name, lon, lat in rows
    ]


@router.get("", response_model=list[ParcelOut])
def list_parcels_for_case(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every parcel attached to one case, for the case detail page."""
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    rows = (
        db.query(Parcel, Person.name, ST_X(Parcel.geom), ST_Y(Parcel.geom))
        .join(Person, Parcel.owner_id == Person.id)
        .filter(Parcel.case_id == case_id)
        .order_by(Parcel.survey_number)
        .all()
    )
    return [
        ParcelOut(
            id=parcel.id,
            case_id=parcel.case_id,
            survey_number=parcel.survey_number,
            area_ha=parcel.area_ha,
            status=parcel.status,
            owner_id=parcel.owner_id,
            owner_name=owner_name,
            longitude=float(lon),
            latitude=float(lat),
        )
        for parcel, owner_name, lon, lat in rows
    ]
