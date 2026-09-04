"""Comparing what the revenue portal says against what the acquisition file says.

This is where the integration earns its place. Pulling a record is plumbing;
the useful act is telling an officer, before an award is passed, that the
portal shows a different surveyed area, a different owner, a pending mutation
or a registered encumbrance on land the state is about to take.

**It never writes.** Every function here returns findings. Correcting a parcel
is a decision an officer makes through the ordinary audited PATCH route, with
their name against it — not something an upstream fetch does on their behalf.
An integration that silently rewrites a notified area is an integration that
loses a case in court.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.integrations.base import (
    LandRecordNotFound,
    LandRecordUnavailable,
    LandRecordsProvider,
    UpstreamLandRecord,
)
from app.models import Case, Parcel, Person, Village

# How far the two areas may differ before it is worth reporting. Survey
# instruments and revenue records genuinely disagree at the margin, and a
# reconciliation that flags every parcel by 0.1% is one nobody reads. One
# percent is tight enough to catch a real transcription error and loose
# enough to stay quiet about measurement noise.
AREA_TOLERANCE_FRACTION = 0.01

MATCHED = "matched"
AREA_MISMATCH = "area_mismatch"
OWNER_MISMATCH = "owner_mismatch"
NOT_FOUND_UPSTREAM = "not_found_upstream"
UNAVAILABLE = "unavailable"


@dataclass
class ParcelReconciliation:
    parcel_id: int
    survey_number: str
    status: str
    # What we hold.
    local_owner_name: str
    local_area_ha: float
    # What the portal holds. None when the portal had nothing to say.
    upstream_owner_name: str | None = None
    upstream_area_ha: float | None = None
    land_classification: str | None = None
    encumbrance: str | None = None
    mutation_pending: bool = False
    record_as_of: str | None = None
    # Human-readable summary of the difference, for the case page.
    note: str | None = None

    @property
    def needs_attention(self) -> bool:
        return self.status != MATCHED or self.encumbrance is not None or self.mutation_pending


def _normalise_name(name: str) -> str:
    """Fold the differences that are not differences.

    Honorifics, case and spacing vary between a revenue record and an
    acquisition file for the same person. Folding them keeps the finding list
    down to names that genuinely differ — but the fold is deliberately
    shallow: it does not try to decide that "Manjunath Gowda" and "M. Gowda"
    are the same person, because that is a judgement with a family's
    compensation attached and it belongs to an officer.
    """
    cleaned = name.strip().lower()
    for honorific in ("smt.", "smt ", "shri.", "shri ", "sri.", "sri ", "mr.", "mrs."):
        if cleaned.startswith(honorific):
            cleaned = cleaned[len(honorific):]
    return " ".join(cleaned.split())


def compare(parcel: Parcel, owner_name: str, upstream: UpstreamLandRecord) -> ParcelReconciliation:
    """One parcel against one upstream record."""
    result = ParcelReconciliation(
        parcel_id=parcel.id,
        survey_number=parcel.survey_number,
        status=MATCHED,
        local_owner_name=owner_name,
        local_area_ha=parcel.area_ha,
        upstream_owner_name=upstream.owner_name,
        upstream_area_ha=upstream.area_ha,
        land_classification=upstream.land_classification,
        encumbrance=upstream.encumbrance,
        mutation_pending=upstream.mutation_pending,
        record_as_of=upstream.record_as_of.isoformat() if upstream.record_as_of else None,
    )

    if parcel.area_ha > 0:
        drift = abs(upstream.area_ha - parcel.area_ha) / parcel.area_ha
        if drift > AREA_TOLERANCE_FRACTION:
            result.status = AREA_MISMATCH
            result.note = (
                f"Revenue record shows {upstream.area_ha:.4f} ha against "
                f"{parcel.area_ha:.4f} ha on file — a {drift * 100:.1f}% difference."
            )
            return result

    if _normalise_name(owner_name) != _normalise_name(upstream.owner_name):
        result.status = OWNER_MISMATCH
        result.note = (
            f"Revenue record names the holder as '{upstream.owner_name}'; "
            f"the acquisition file has '{owner_name}'."
        )
        return result

    if upstream.mutation_pending:
        result.note = "Matched, but a mutation is pending at the revenue office."
    elif upstream.encumbrance:
        result.note = f"Matched, but an encumbrance is registered: {upstream.encumbrance}"
    return result


def reconcile_case(
    db: Session,
    provider: LandRecordsProvider,
    case_id: int,
) -> dict:
    """Every parcel on a case, checked against the portal.

    Entitlement is the caller's job — this is handed a case_id that has
    already been scoped, and does not re-derive who may see what.
    """
    village_lgd = (
        db.query(Village.lgd_code)
        .join(Case, Case.village_id == Village.id)
        .filter(Case.id == case_id)
        .scalar()
    )

    rows = (
        db.query(Parcel, Person.name)
        .join(Person, Parcel.owner_id == Person.id)
        .filter(Parcel.case_id == case_id)
        .order_by(Parcel.survey_number)
        .all()
    )

    results: list[ParcelReconciliation] = []
    for parcel, owner_name in rows:
        if not village_lgd:
            # No LGD code means no join key, and guessing by name is exactly
            # the heuristic this design exists to avoid.
            results.append(
                ParcelReconciliation(
                    parcel_id=parcel.id,
                    survey_number=parcel.survey_number,
                    status=UNAVAILABLE,
                    local_owner_name=owner_name,
                    local_area_ha=parcel.area_ha,
                    note="The case's village has no LGD code, so there is no key to look it up by.",
                )
            )
            continue
        try:
            upstream = provider.fetch(village_lgd, parcel.survey_number)
        except LandRecordNotFound:
            results.append(
                ParcelReconciliation(
                    parcel_id=parcel.id,
                    survey_number=parcel.survey_number,
                    status=NOT_FOUND_UPSTREAM,
                    local_owner_name=owner_name,
                    local_area_ha=parcel.area_ha,
                    note=(
                        "The revenue portal has no parcel with this survey number. "
                        "Usually a subdivision that was renumbered at source."
                    ),
                )
            )
            continue
        except LandRecordUnavailable as exc:
            results.append(
                ParcelReconciliation(
                    parcel_id=parcel.id,
                    survey_number=parcel.survey_number,
                    status=UNAVAILABLE,
                    local_owner_name=owner_name,
                    local_area_ha=parcel.area_ha,
                    note=f"The revenue portal could not be reached: {exc}",
                )
            )
            continue
        results.append(compare(parcel, owner_name, upstream))

    by_status: dict[str, int] = {}
    for item in results:
        by_status[item.status] = by_status.get(item.status, 0) + 1

    return {
        "case_id": case_id,
        "village_lgd": village_lgd,
        "provider": provider.info.key,
        "provider_label": provider.info.label,
        "is_live": provider.info.is_live,
        "parcels_checked": len(results),
        "needs_attention": sum(1 for r in results if r.needs_attention),
        "by_status": by_status,
        "items": results,
    }
