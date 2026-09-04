"""External land-record and cadastral integration.

Read-only by design. See app/integrations/__init__.py for why the provider is
a port with a mock behind it, and app/services/landrecords.py for why nothing
here writes back to a parcel.

Every call is audited. Querying a citizen's landholding against a revenue
portal is a lookup of personal data about a named person, and "who asked the
portal about this parcel, and when" is precisely the kind of thing an audit
trail exists to answer.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.integrations import available_providers, get_provider
from app.integrations.base import LandRecordNotFound, LandRecordUnavailable
from app.integrations.providers import configured_key
from app.models import Case, User, Village
from app.schemas.integration import (
    ProviderList,
    ProviderOut,
    ReconciliationItem,
    ReconciliationReport,
    UpstreamRecordOut,
)
from app.services import audit, landrecords

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Who may query an external portal. Deliberately not every signed-in role: a
# landowner has no business running lookups against other people's holdings,
# and a requiring body is a petitioner, not an office of the revenue system.
LOOKUP_ROLES = (
    Role.ADMIN,
    Role.DISTRICT_OFFICER,
    Role.SLAO,
    Role.FIELD_OFFICER,
    Role.RNR_OFFICER,
    Role.STATE_OFFICER,
    Role.MINISTRY_OFFICER,
)


@router.get("/providers", response_model=ProviderList)
def list_providers(user: User = Depends(get_current_user)):
    """What this build can talk to, and which one is wired in.

    `is_live` is the field that matters: it is false for the mock, and the
    frontend badges every screen that shows upstream data with it. A demo
    that cannot tell you its data is simulated is a demo that is lying.
    """
    return ProviderList(
        items=[
            ProviderOut(
                key=info.key,
                label=info.label,
                authority=info.authority,
                is_live=info.is_live,
                covers_states=list(info.covers_states),
            )
            for info in available_providers()
        ],
        configured=configured_key(),
    )


@router.get("/land-records", response_model=UpstreamRecordOut)
def lookup_land_record(
    village_lgd: str = Query(
        min_length=1,
        max_length=10,
        description="Local Government Directory code of the village — the join key.",
    ),
    survey_number: str = Query(min_length=1, max_length=20),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*LOOKUP_ROLES)),
):
    """One parcel as the external portal describes it."""
    provider = get_provider(db)
    try:
        record = provider.fetch(village_lgd, survey_number)
    except LandRecordNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LandRecordUnavailable as exc:
        # 502, not 404: the portal failing is an upstream problem, and
        # reporting it as "no such parcel" would let an outage read as a
        # finding about the land.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The land-record portal could not be reached: {exc}",
        ) from exc

    audit.record(
        db,
        user,
        action="integration.land_record_lookup",
        entity_type="village",
        detail=f"provider={provider.info.key} village_lgd={village_lgd} survey={survey_number}",
    )
    db.commit()

    return UpstreamRecordOut(
        village_lgd=record.village_lgd,
        survey_number=record.survey_number,
        owner_name=record.owner_name,
        area_ha=record.area_ha,
        land_classification=record.land_classification,
        encumbrance=record.encumbrance,
        mutation_pending=record.mutation_pending,
        record_as_of=record.record_as_of.isoformat() if record.record_as_of else None,
        provider=provider.info.key,
        is_live=provider.info.is_live,
    )


@router.get("/reconcile", response_model=ReconciliationReport)
def reconcile(
    case_id: int = Query(description="Case whose parcels to check against the portal"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*LOOKUP_ROLES)),
):
    """Check every parcel on a case against the revenue record.

    The useful half of the integration. Pulling one record is plumbing; this
    tells an officer, before an award is passed, which parcels the portal
    disagrees with and why.
    """
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    provider = get_provider(db)
    report = landrecords.reconcile_case(db, provider, case_id)

    audit.record(
        db,
        user,
        action="integration.reconcile",
        entity_type="case",
        entity_id=case_id,
        detail=(
            f"provider={report['provider']} checked={report['parcels_checked']} "
            f"needs_attention={report['needs_attention']}"
        ),
    )
    db.commit()

    return ReconciliationReport(
        case_id=report["case_id"],
        village_lgd=report["village_lgd"],
        provider=report["provider"],
        provider_label=report["provider_label"],
        is_live=report["is_live"],
        parcels_checked=report["parcels_checked"],
        needs_attention=report["needs_attention"],
        by_status=report["by_status"],
        items=[
            ReconciliationItem(
                parcel_id=item.parcel_id,
                survey_number=item.survey_number,
                status=item.status,
                local_owner_name=item.local_owner_name,
                local_area_ha=item.local_area_ha,
                upstream_owner_name=item.upstream_owner_name,
                upstream_area_ha=item.upstream_area_ha,
                land_classification=item.land_classification,
                encumbrance=item.encumbrance,
                mutation_pending=item.mutation_pending,
                record_as_of=item.record_as_of,
                note=item.note,
                needs_attention=item.needs_attention,
            )
            for item in report["items"]
        ],
    )
