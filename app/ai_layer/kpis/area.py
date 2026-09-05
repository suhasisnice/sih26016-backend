"""KPI 1 -- area notified vs area acquired, in hectares.

Every parcel row implies its land was notified (a parcel is only created off
the back of a preliminary notification), so "notified" is every parcel in
scope. "Acquired" narrows to parcels whose acquisition is actually complete.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import ParcelStatus
from app.models import Parcel

ACQUIRED_STATUSES = (ParcelStatus.ACQUIRED, ParcelStatus.POSSESSION_TAKEN)


def area_kpis(db: Session, case_ids: list[int]) -> dict:
    if not case_ids:
        return {"area_notified_ha": 0.0, "area_acquired_ha": 0.0}

    notified = db.query(func.coalesce(func.sum(Parcel.area_ha), 0.0)).filter(
        Parcel.case_id.in_(case_ids)
    ).scalar()
    acquired = (
        db.query(func.coalesce(func.sum(Parcel.area_ha), 0.0))
        .filter(Parcel.case_id.in_(case_ids), Parcel.status.in_(ACQUIRED_STATUSES))
        .scalar()
    )
    return {
        "area_notified_ha": round(float(notified), 4),
        "area_acquired_ha": round(float(acquired), 4),
    }
