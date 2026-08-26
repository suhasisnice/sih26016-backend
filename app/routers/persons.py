"""Affected people for a case, with compensation and R&R side by side.

The one screen where the Act's central distinction becomes visible: a
tenant farmer appears with no compensation and a live R&R entitlement. The
two are returned as separate objects and never reconciled into a single
status, because they answer different questions and can disagree.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db, scope_cases_to_user
from app.models import AffectedFamily, Case, Compensation, Parcel, Person, RnRRecord, User, Village
from app.schemas.person import AffectedPersonList, AffectedPersonOut, CompensationOut, RnROut

router = APIRouter(prefix="/persons", tags=["persons"])


@router.get("", response_model=AffectedPersonList)
def list_affected_people(
    case_id: int = Query(description="Case whose affected households to list"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    rows = (
        db.query(AffectedFamily, Person, Village.name)
        .join(Person, AffectedFamily.person_id == Person.id)
        .join(Village, Person.village_id == Village.id)
        .filter(AffectedFamily.case_id == case_id)
        .order_by(AffectedFamily.is_landowner.desc(), Person.name)
        .all()
    )
    if not rows:
        return AffectedPersonList(items=[], total=0, landowner_count=0, landless_count=0)

    person_ids = [person.id for _, person, _ in rows]

    # Three grouped lookups, rather than three queries per person.
    parcels = {
        person_id: (count, round(float(area), 4))
        for person_id, count, area in db.query(
            Parcel.owner_id, func.count(Parcel.id), func.coalesce(func.sum(Parcel.area_ha), 0.0)
        )
        .filter(Parcel.case_id == case_id, Parcel.owner_id.in_(person_ids))
        .group_by(Parcel.owner_id)
        .all()
    }
    compensation = {
        row.person_id: row
        for row in db.query(Compensation)
        .filter(Compensation.case_id == case_id, Compensation.person_id.in_(person_ids))
        .all()
    }
    rnr = {
        row.person_id: row
        for row in db.query(RnRRecord)
        .filter(RnRRecord.case_id == case_id, RnRRecord.person_id.in_(person_ids))
        .all()
    }

    items = []
    for family, person, village_name in rows:
        comp = compensation.get(person.id)
        entitlement = rnr.get(person.id)
        parcel_count, total_area = parcels.get(person.id, (0, 0.0))

        items.append(
            AffectedPersonOut(
                person_id=person.id,
                name=person.name,
                village_name=village_name,
                has_land_title=person.has_land_title,
                is_landowner=family.is_landowner,
                parcel_count=parcel_count,
                total_area_ha=total_area,
                compensation=(
                    CompensationOut(
                        id=comp.id,
                        amount_awarded=comp.amount_awarded,
                        amount_paid=comp.amount_paid,
                        amount_pending=comp.amount_awarded - comp.amount_paid,
                        status=comp.status,
                        awarded_on=comp.awarded_on,
                    )
                    if comp
                    else None
                ),
                rnr=(
                    RnROut(
                        id=entitlement.id,
                        status=entitlement.status,
                        entitlement=entitlement.entitlement,
                        updated_on=entitlement.updated_on,
                    )
                    if entitlement
                    else None
                ),
            )
        )

    landowners = sum(1 for item in items if item.is_landowner)
    return AffectedPersonList(
        items=items,
        total=len(items),
        landowner_count=landowners,
        landless_count=len(items) - landowners,
    )
