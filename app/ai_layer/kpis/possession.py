from sqlalchemy import case, func

from app.core.enums import ParcelStatus
from app.models import Parcel


def compute_possession(db, case_ids: list[int]) -> dict:
    """KPI 5 - possession, counted in parcels rather than cases.

    A case is rarely all-or-nothing: some parcels are handed over while
    others are still being cleared, so counting whole cases would over- or
    understate progress depending on which way you rounded.
    """
    if not case_ids:
        return {"possession_taken_count": 0, "possession_pending_count": 0}

    total, taken = db.query(
        func.count(Parcel.id),
        func.coalesce(
            func.sum(case((Parcel.status == ParcelStatus.POSSESSION_TAKEN, 1), else_=0)), 0
        ),
    ).filter(Parcel.case_id.in_(case_ids)).one()

    return {
        "possession_taken_count": int(taken),
        "possession_pending_count": int(total) - int(taken),
    }
