import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from geoalchemy2.functions import ST_AsGeoJSON, ST_MakeEnvelope, ST_X, ST_Y
from sqlalchemy.orm import Session

from app.core.enums import MutationStatus, ParcelStatus, Role
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.integrations.providers import configured_key, get_provider
from app.models import Case, MutationRequest, Parcel, Person, User
from app.schemas import (
    ParcelFeature,
    ParcelFeatureCollection,
    ParcelOut,
)
from app.schemas.geo import (
    ParcelCreate,
    ParcelProperties,
    ParcelUpdate,
    PointGeometry,
    PolygonGeometry,
)
from app.schemas.mutation import MutationRequestList, MutationRequestOut
from app.services import audit

router = APIRouter(prefix="/parcels", tags=["parcels"])

# Who may register or correct a parcel. A field officer is the point of this
# list: they are the person standing in the field with the GPS, and until
# now the role could not record anything about a parcel at all.
PARCEL_WRITERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO, Role.FIELD_OFFICER)

# A map viewport can cover thousands of parcels. Capping the response keeps
# one careless zoom-out from pulling the whole country into the browser;
# the payload says when it was capped so the map can ask the user to zoom.
BBOX_FEATURE_LIMIT = 1000


def _visible_case_ids(db: Session, user: User):
    return scope_cases_to_user(db.query(Case.id), user).subquery().select()


def _geometry(boundary_geojson: str | None, lon: float, lat: float):
    """The best shape we have for this parcel.

    The surveyed outline when there is one, the GPS fix when there is not.
    ST_AsGeoJSON hands back the geometry member as a JSON string — parsed
    rather than passed through as text, because the response model has to
    validate it and a string would serialise as a quoted blob the map would
    have to JSON.parse a second time.
    """
    if boundary_geojson:
        return PolygonGeometry(coordinates=json.loads(boundary_geojson)["coordinates"])
    return PointGeometry(coordinates=[lon, lat])


@router.get("/bbox", response_model=ParcelFeatureCollection)
def parcels_in_bbox(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    min_lon: float = Query(ge=-180, le=180),
    min_lat: float = Query(ge=-90, le=90),
    max_lon: float = Query(ge=-180, le=180),
    max_lat: float = Query(ge=-90, le=90),
    parcel_status: ParcelStatus | None = None,
    case_id: int | None = Query(
        default=None,
        description="Only this case's parcels — what 'show me this project's plots' asks for.",
    ),
):
    """Parcels inside the map's current viewport, as GeoJSON.

    The spatial filter runs in PostGIS against the GiST index, so the
    database returns only what is on screen instead of the API loading
    every parcel and discarding most of them.

    Each feature's geometry is the parcel's surveyed outline where one is on
    file, and its GPS fix where one is not. The filter above tests the fix
    either way: a parcel is a few hundred metres across, so filtering by
    centre and filtering by outline select the same rows at every zoom a
    person actually uses, and only the centre has an index that a bounding
    box can drive. The alternative — COALESCE(boundary, geom) — is correct to
    the pixel and gives up the index scan to be so.
    """
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_lon/min_lat must be smaller than max_lon/max_lat",
        )

    envelope = ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
    query = (
        db.query(
            Parcel,
            Case.case_number,
            Person.name,
            ST_X(Parcel.geom),
            ST_Y(Parcel.geom),
            ST_AsGeoJSON(Parcel.boundary),
        )
        .join(Case, Parcel.case_id == Case.id)
        .join(Person, Parcel.owner_id == Person.id)
        .filter(Parcel.geom.ST_Intersects(envelope))
        .filter(Parcel.case_id.in_(_visible_case_ids(db, user)))
    )
    if parcel_status is not None:
        query = query.filter(Parcel.status == parcel_status)
    if case_id is not None:
        # Not checked against the entitlement separately: the case-id filter
        # can only narrow the visible set above, so an unentitled case_id
        # returns nothing rather than leaking that it exists.
        query = query.filter(Parcel.case_id == case_id)

    # Fetch one extra to detect truncation without a second count query.
    rows = query.order_by(Parcel.id).limit(BBOX_FEATURE_LIMIT + 1).all()
    truncated = len(rows) > BBOX_FEATURE_LIMIT
    rows = rows[:BBOX_FEATURE_LIMIT]

    features = [
        ParcelFeature(
            geometry=_geometry(boundary, float(lon), float(lat)),
            properties=ParcelProperties(
                id=parcel.id,
                case_id=parcel.case_id,
                case_number=case_number,
                survey_number=parcel.survey_number,
                ulpin=parcel.ulpin,
                area_ha=parcel.area_ha,
                status=parcel.status,
                owner_name=owner_name,
                longitude=float(lon),
                latitude=float(lat),
                has_boundary=boundary is not None,
            ),
        )
        for parcel, case_number, owner_name, lon, lat, boundary in rows
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
            ulpin=parcel.ulpin,
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
            ulpin=parcel.ulpin,
            area_ha=parcel.area_ha,
            status=parcel.status,
            owner_id=parcel.owner_id,
            owner_name=owner_name,
            longitude=float(lon),
            latitude=float(lat),
        )
        for parcel, owner_name, lon, lat in rows
    ]


@router.get("/{parcel_id}", response_model=ParcelOut)
def get_parcel(
    parcel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One parcel, for the parcel detail page and the map popup's link.

    Declared after /bbox and /search so those literal paths are matched
    before this one treats "bbox" as an id.
    """
    row = (
        db.query(Parcel, Person.name, ST_X(Parcel.geom), ST_Y(Parcel.geom))
        .join(Person, Parcel.owner_id == Person.id)
        .filter(Parcel.id == parcel_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")

    parcel, owner_name, lon, lat = row

    # Scoped through the parcel's case, so a landowner cannot read a parcel
    # in a district they have nothing to do with by guessing ids.
    visible = scope_cases_to_user(db.query(Case), user).filter(Case.id == parcel.case_id).first()
    if visible is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")

    return ParcelOut(
        id=parcel.id,
        case_id=parcel.case_id,
        survey_number=parcel.survey_number,
        ulpin=parcel.ulpin,
        area_ha=parcel.area_ha,
        status=parcel.status,
        owner_id=parcel.owner_id,
        owner_name=owner_name,
        longitude=float(lon),
        latitude=float(lat),
    )


def _parcel_out(parcel: Parcel, owner_name: str, lon: float, lat: float) -> ParcelOut:
    """One place that builds the parcel response, so the four routes that
    return one cannot drift into four slightly different shapes."""
    return ParcelOut(
        id=parcel.id,
        case_id=parcel.case_id,
        survey_number=parcel.survey_number,
        ulpin=parcel.ulpin,
        area_ha=parcel.area_ha,
        status=parcel.status,
        owner_id=parcel.owner_id,
        owner_name=owner_name,
        longitude=float(lon),
        latitude=float(lat),
    )


def _reload_parcel(db: Session, parcel_id: int) -> ParcelOut:
    row = (
        db.query(Parcel, Person.name, ST_X(Parcel.geom), ST_Y(Parcel.geom))
        .join(Person, Parcel.owner_id == Person.id)
        .filter(Parcel.id == parcel_id)
        .first()
    )
    parcel, owner_name, lon, lat = row
    return _parcel_out(parcel, owner_name, lon, lat)


@router.post("", response_model=ParcelOut, status_code=status.HTTP_201_CREATED)
def create_parcel(
    payload: ParcelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PARCEL_WRITERS)),
):
    """Register a parcel where it stands — the field-collection path.

    Geo-tagging was read-only before this: every coordinate in the system
    came from the seed, so "GIS-enabled geo-tagging" was a map of data
    nobody could add to. The coordinates arrive from the device and are
    written straight to PostGIS as an EWKT point, which is what the bbox
    query and the map already read.
    """
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == payload.case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    owner = db.get(Person, payload.owner_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown owner_id")

    # A survey number is unique within a case: the same number twice is a
    # duplicate entry, and duplicated parcels double-count area on every
    # dashboard figure that sums them.
    duplicate = (
        db.query(Parcel.id)
        .filter(Parcel.case_id == payload.case_id, Parcel.survey_number == payload.survey_number)
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Survey number '{payload.survey_number}' is already recorded on this case",
        )

    # Law 3: one ULPIN, one parcel, nationally — never scoped to a case the
    # way survey_number is, because the whole point of the identifier is
    # that it means the same plot everywhere in the country.
    if payload.ulpin is not None:
        existing_ulpin = db.query(Parcel.id).filter(Parcel.ulpin == payload.ulpin).first()
        if existing_ulpin:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ULPIN '{payload.ulpin}' is already recorded against another parcel",
            )

    parcel = Parcel(
        case_id=payload.case_id,
        survey_number=payload.survey_number,
        ulpin=payload.ulpin,
        area_ha=payload.area_ha,
        owner_id=payload.owner_id,
        status=payload.status,
        geom=f"SRID=4326;POINT({payload.longitude} {payload.latitude})",
    )
    db.add(parcel)
    db.flush()

    audit.record(
        db,
        user,
        action="parcel.create",
        entity_type="parcel",
        entity_id=parcel.id,
        detail=(
            f"case={payload.case_id} survey={payload.survey_number} "
            f"area={payload.area_ha}ha at ({payload.latitude:.5f},{payload.longitude:.5f})"
            + (f" +/-{payload.gps_accuracy_m}m" if payload.gps_accuracy_m is not None else "")
        ),
    )
    db.commit()
    return _reload_parcel(db, parcel.id)


@router.patch("/{parcel_id}", response_model=ParcelOut)
def update_parcel(
    parcel_id: int,
    payload: ParcelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PARCEL_WRITERS)),
):
    """Correct a parcel, or move its acquisition status along.

    Latitude and longitude move together or not at all. Accepting one alone
    would place the parcel on a line through the original point, which is a
    silently wrong location rather than an obviously missing one.
    """
    parcel = db.get(Parcel, parcel_id)
    if parcel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")

    # Entitlement is checked against the case, not the parcel: knowing a
    # parcel id must never be enough to edit another district's land record.
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == parcel.case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    has_lon = fields.get("longitude") is not None
    has_lat = fields.get("latitude") is not None
    if has_lon != has_lat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send longitude and latitude together, or neither",
        )

    changes = []
    if has_lon and has_lat:
        parcel.geom = f"SRID=4326;POINT({fields['longitude']} {fields['latitude']})"
        changes.append(f"geom=({fields['latitude']:.5f},{fields['longitude']:.5f})")

    if "ulpin" in fields and fields["ulpin"] is not None and fields["ulpin"] != parcel.ulpin:
        existing_ulpin = (
            db.query(Parcel.id)
            .filter(Parcel.ulpin == fields["ulpin"], Parcel.id != parcel.id)
            .first()
        )
        if existing_ulpin:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ULPIN '{fields['ulpin']}' is already recorded against another parcel",
            )

    for key in ("survey_number", "ulpin", "area_ha", "status"):
        if key in fields and fields[key] is not None:
            value = fields[key]
            if getattr(parcel, key) != value:
                setattr(parcel, key, value)
                changes.append(f"{key}={value.value if hasattr(value, 'value') else value}")

    if not changes:
        return _reload_parcel(db, parcel.id)

    audit.record(
        db,
        user,
        action="parcel.update",
        entity_type="parcel",
        entity_id=parcel.id,
        detail=", ".join(changes),
    )
    db.commit()
    return _reload_parcel(db, parcel.id)


@router.get("/{parcel_id}/mutation-requests", response_model=MutationRequestList)
def list_mutation_requests(
    parcel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Every push to the land-record portal for one parcel, most recent
    first — so an officer can see whether a stalled mutation was ever
    actually sent, and what the portal said when it was."""
    parcel = db.get(Parcel, parcel_id)
    if parcel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")
    visible = scope_cases_to_user(db.query(Case), user).filter(Case.id == parcel.case_id).first()
    if visible is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")

    rows = (
        db.query(MutationRequest)
        .filter(MutationRequest.parcel_id == parcel_id)
        .order_by(MutationRequest.created_at.desc())
        .all()
    )
    return MutationRequestList(
        items=[MutationRequestOut.model_validate(row) for row in rows], total=len(rows)
    )


@router.post(
    "/{parcel_id}/mutation-request",
    response_model=MutationRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_mutation(
    parcel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PARCEL_WRITERS)),
):
    """Push a mutation request: tell the state's land-record portal that
    government now holds this parcel.

    Refuses before possession — a mutation pushed on land not yet taken
    would tell the revenue record something that has not happened yet, and
    the acquisition file would be the one place recording it as if it had.
    """
    parcel = db.get(Parcel, parcel_id)
    if parcel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == parcel.case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcel not found")

    if parcel.status != ParcelStatus.POSSESSION_TAKEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Possession must be taken before a mutation request can be sent",
        )

    provider = get_provider(db)
    ack = provider.push_mutation(ulpin=parcel.ulpin or "", survey_number=parcel.survey_number)

    mutation = MutationRequest(
        parcel_id=parcel.id,
        case_id=case.id,
        ulpin=parcel.ulpin,
        adapter=configured_key(),
        sent_on=date.today(),
        external_ref=ack.external_ref,
        status=MutationStatus(ack.status),
        response_payload=ack.raw,
        requested_by_user_id=user.id,
    )
    db.add(mutation)
    db.flush()

    audit.record(
        db,
        user,
        action="mutation.request",
        entity_type="parcel",
        entity_id=parcel.id,
        detail=f"adapter={configured_key()} status={ack.status} ref={ack.external_ref or '—'}",
    )
    db.commit()
    db.refresh(mutation)
    return MutationRequestOut.model_validate(mutation)
