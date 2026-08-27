"""Affected people for a case, with compensation and R&R side by side.

The one screen where the Act's central distinction becomes visible: a
tenant farmer appears with no compensation and a live R&R entitlement. The
two are returned as separate objects and never reconciled into a single
status, because they answer different questions and can disagree.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import CompensationStatus, Role, RnRStatus
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.models import AffectedFamily, Case, Compensation, Parcel, Person, RnRRecord, User, Village
from app.schemas.person import (
    AffectedPersonCreate,
    AffectedPersonList,
    AffectedPersonOut,
    CompensationOut,
    CompensationUpdate,
    RnROut,
    RnRUpdate,
)
from app.services import audit

router = APIRouter(prefix="/persons", tags=["persons"])

# Compensation and R&R hang off their own paths rather than /persons/...
# because the frontend edits a record by its own id, and nesting them
# under a person would imply one each per person — a household can have
# an R&R entitlement and no compensation at all.
compensation_router = APIRouter(prefix="/compensation", tags=["persons"])
rnr_router = APIRouter(prefix="/rnr", tags=["persons"])


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


# Compensation and R&R are edited by different offices in practice, so they
# are guarded separately rather than sharing one "money" role. An R&R
# officer must not be able to move an award, and an SLAO must not quietly
# close a resettlement entitlement.
COMPENSATION_WRITERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)
RNR_WRITERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.RNR_OFFICER)
PERSON_WRITERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO, Role.FIELD_OFFICER)


def _visible_case_or_404(db: Session, user: User, case_id: int) -> Case:
    """404 rather than 403 for a case outside the user's scope, matching
    cases.py: a 403 confirms the case exists, which is itself something a
    landowner should not be able to fish for by trying ids."""
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


@compensation_router.patch("/{compensation_id}", response_model=CompensationOut)
def update_compensation(
    compensation_id: int,
    payload: CompensationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*COMPENSATION_WRITERS)),
):
    """Record an award or a payment against one household."""
    record = db.get(Compensation, compensation_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compensation record not found")
    _visible_case_or_404(db, user, record.case_id)

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    awarded = fields.get("amount_awarded", record.amount_awarded)
    paid = fields.get("amount_paid", record.amount_paid)
    if paid > awarded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Amount paid (₹{paid:,}) cannot exceed the amount awarded (₹{awarded:,})",
        )

    for key, value in fields.items():
        setattr(record, key, value)

    # Paying an award in full settles it. Left to the client this drifts —
    # one screen marks it paid, another forgets, and the dashboard's
    # awarded-vs-paid figure stops agreeing with the rows beneath it.
    if paid >= awarded and awarded > 0 and "status" not in fields:
        record.status = CompensationStatus.PAID

    audit.record(
        db,
        user,
        action="compensation.update",
        entity_type="compensation",
        entity_id=record.id,
        detail=f"awarded={record.amount_awarded} paid={record.amount_paid} status={record.status.value}",
    )
    db.commit()
    db.refresh(record)

    return CompensationOut(
        id=record.id,
        amount_awarded=record.amount_awarded,
        amount_paid=record.amount_paid,
        amount_pending=record.amount_awarded - record.amount_paid,
        status=record.status,
        awarded_on=record.awarded_on,
    )


@rnr_router.patch("/{rnr_id}", response_model=RnROut)
def update_rnr(
    rnr_id: int,
    payload: RnRUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*RNR_WRITERS)),
):
    """Move a resettlement entitlement along, independently of any award."""
    record = db.get(RnRRecord, rnr_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="R&R record not found")
    _visible_case_or_404(db, user, record.case_id)

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    for key, value in fields.items():
        setattr(record, key, value)
    record.updated_on = date.today()

    audit.record(
        db,
        user,
        action="rnr.update",
        entity_type="rnr",
        entity_id=record.id,
        detail=f"status={record.status.value}",
    )
    db.commit()
    db.refresh(record)
    return RnROut(
        id=record.id,
        status=record.status,
        entitlement=record.entitlement,
        updated_on=record.updated_on,
    )


@router.post("", response_model=AffectedPersonOut, status_code=status.HTTP_201_CREATED)
def add_affected_person(
    payload: AffectedPersonCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PERSON_WRITERS)),
):
    """Add a household to a case, with an R&R entitlement opened for them.

    An R&R record is created for every affected household regardless of
    title, because resettlement is owed on displacement, not ownership. No
    compensation record is created here — that follows an award, and
    creating an empty one would show ₹0 awarded, which reads as a decision
    rather than an absence.
    """
    case = _visible_case_or_404(db, user, payload.case_id)

    village = db.get(Village, payload.village_id)
    if village is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Village not found")
    if village.district_id != case.district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Village is not in the same district as the case",
        )

    person = Person(
        name=payload.name,
        village_id=payload.village_id,
        phone=payload.phone,
        has_land_title=payload.has_land_title,
    )
    db.add(person)
    db.flush()

    db.add(AffectedFamily(case_id=case.id, person_id=person.id, is_landowner=payload.is_landowner))
    db.add(
        RnRRecord(
            case_id=case.id,
            person_id=person.id,
            status=RnRStatus.PENDING,
            entitlement=payload.rnr_entitlement,
            updated_on=date.today(),
        )
    )

    audit.record(
        db,
        user,
        action="person.create",
        entity_type="person",
        entity_id=person.id,
        detail=f"case={case.id} landowner={payload.is_landowner} title={payload.has_land_title}",
    )
    db.commit()
    db.refresh(person)

    entitlement = (
        db.query(RnRRecord)
        .filter(RnRRecord.case_id == case.id, RnRRecord.person_id == person.id)
        .first()
    )
    return AffectedPersonOut(
        person_id=person.id,
        name=person.name,
        village_name=village.name,
        has_land_title=person.has_land_title,
        is_landowner=payload.is_landowner,
        parcel_count=0,
        total_area_ha=0.0,
        compensation=None,
        rnr=RnROut(
            id=entitlement.id,
            status=entitlement.status,
            entitlement=entitlement.entitlement,
            updated_on=entitlement.updated_on,
        ),
    )
