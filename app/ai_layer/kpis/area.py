from sqlalchemy import case, func

from app.core.enums import ParcelStatus
from app.models import Parcel

# A parcel counts as acquired once the award has cleared it; land already
# handed over is certainly acquired too.
ACQUIRED_STATUSES = (ParcelStatus.ACQUIRED, ParcelStatus.POSSESSION_TAKEN)


def compute_area(db, case_ids: list[int]) -> dict:
    """KPI 1 - hectares notified and acquired.

    Notified counts every parcel in scope: a parcel is in the system
    because it was named in a preliminary notification.
    """
    if not case_ids:
        return {"area_notified_ha": 0.0, "area_acquired_ha": 0.0}

    notified, acquired = db.query(
        func.coalesce(func.sum(Parcel.area_ha), 0.0),
        func.coalesce(
            func.sum(case((Parcel.status.in_(ACQUIRED_STATUSES), Parcel.area_ha), else_=0.0)),
            0.0,
        ),
    ).filter(Parcel.case_id.in_(case_ids)).one()

    return {
        "area_notified_ha": round(float(notified), 4),
        "area_acquired_ha": round(float(acquired), 4),
    }
