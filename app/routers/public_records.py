"""Read-only API for verified public-source acquisition records."""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models import PublicAcquisitionRecord, User
from app.schemas.public_records import PublicAcquisitionRecordOut

router = APIRouter(prefix="/public-acquisitions", tags=["public-acquisitions"])


@router.get("", response_model=list[PublicAcquisitionRecordOut])
def list_public_acquisitions(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    district: str | None = None,
    record_type: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
):
    """List curated public records with lightweight dashboard filters."""
    limit = min(max(limit, 1), 500)
    query = db.query(PublicAcquisitionRecord).filter(
        PublicAcquisitionRecord.is_verified_public.is_(True)
    )
    if district:
        query = query.filter(PublicAcquisitionRecord.district.ilike(district))
    if record_type:
        query = query.filter(PublicAcquisitionRecord.record_type == record_type)
    if project_id:
        query = query.filter(PublicAcquisitionRecord.project_id == project_id)
    return query.order_by(PublicAcquisitionRecord.id).limit(limit).all()


@router.get("/summary")
def public_acquisition_summary(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Aggregate only values explicitly present in the public-source layer."""
    rows = db.query(
        func.count(PublicAcquisitionRecord.id),
        func.coalesce(func.sum(PublicAcquisitionRecord.area_ha), 0),
        func.coalesce(func.sum(PublicAcquisitionRecord.area_acres), 0),
        func.coalesce(func.sum(PublicAcquisitionRecord.compensation_paid), 0),
    ).filter(PublicAcquisitionRecord.is_verified_public.is_(True)).one()
    return {
        "record_count": rows[0],
        "area_ha_reported": float(rows[1]),
        "area_acres_reported": float(rows[2]),
        "compensation_paid_reported": int(rows[3]),
        "note": "Aggregates include only amounts and areas explicitly present in curated public sources.",
    }
