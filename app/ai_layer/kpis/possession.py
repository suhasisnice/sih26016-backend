"""KPI 5 -- possession, counted in parcels per the problem statement, not
in cases."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import ParcelStatus
from app.models import Parcel


def possession_kpis(db: Session, case_ids: list[int]) -> dict:
    if not case_ids:
        return {"possession_taken_count": 0, "possession_pending_count": 0}

    total = db.query(func.count(Parcel.id)).filter(Parcel.case_id.in_(case_ids)).scalar() or 0
    taken = (
        db.query(func.count(Parcel.id))
        .filter(Parcel.case_id.in_(case_ids), Parcel.status == ParcelStatus.POSSESSION_TAKEN)
        .scalar()
        or 0
    )
    return {
        "possession_taken_count": int(taken),
        "possession_pending_count": int(total) - int(taken),
    }
