"""Read-only API for verified public-source acquisition records.

Unauthenticated, on purpose: everything in this table is sourced from a
government gazette or a published news report — see
data/real_acquisition_seed/README.md's integrity rules — so there is
nothing here an anonymous visitor couldn't already find themselves. The
Case Studies page on the public site reads this before anyone signs in,
the same way /notices does.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models import PublicAcquisitionRecord
from app.schemas.public_records import PublicAcquisitionRecordOut

router = APIRouter(prefix="/public-acquisitions", tags=["public-acquisitions"])


@router.get("", response_model=list[PublicAcquisitionRecordOut])
def list_public_acquisitions(
    db: Session = Depends(get_db),
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
def public_acquisition_summary(db: Session = Depends(get_db)):
    """Summarize public data without double-counting project and parcel rows."""
    project_rows = db.query(
        func.count(PublicAcquisitionRecord.id),
        func.coalesce(func.sum(PublicAcquisitionRecord.area_ha), 0),
        func.coalesce(func.sum(PublicAcquisitionRecord.area_acres), 0),
    ).filter(
        PublicAcquisitionRecord.is_verified_public.is_(True),
        PublicAcquisitionRecord.record_type == "project",
    ).one()
    parcel_rows = db.query(
        func.count(PublicAcquisitionRecord.id),
        func.coalesce(func.sum(PublicAcquisitionRecord.area_ha), 0),
        func.coalesce(func.sum(PublicAcquisitionRecord.area_acres), 0),
    ).filter(
        PublicAcquisitionRecord.is_verified_public.is_(True),
        PublicAcquisitionRecord.record_type == "parcel",
    ).one()
    compensation_rows = db.query(
        func.count(PublicAcquisitionRecord.id),
        func.coalesce(func.sum(PublicAcquisitionRecord.compensation_paid), 0),
    ).filter(
        PublicAcquisitionRecord.is_verified_public.is_(True),
        PublicAcquisitionRecord.record_type == "compensation",
    ).one()
    return {
        "project_records": {"count": project_rows[0], "area_ha_reported": float(project_rows[1]), "area_acres_reported": float(project_rows[2])},
        "parcel_records": {"count": parcel_rows[0], "area_ha_reported": float(parcel_rows[1]), "area_acres_reported": float(parcel_rows[2])},
        "compensation_records": {"count": compensation_rows[0], "compensation_paid_reported": int(compensation_rows[1])},
        "note": "Project and parcel extents are reported separately to avoid double-counting. Compensation totals include only explicitly reported public amounts.",
    }
