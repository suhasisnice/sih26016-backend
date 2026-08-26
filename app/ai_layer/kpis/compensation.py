from sqlalchemy import func

from app.models import Compensation


def compute_compensation(db, case_ids: list[int]) -> dict:
    """KPI 2 - compensation in whole rupees.

    Money for land taken, and nothing else. R&R support is counted
    separately in rnr.py and the two are never added together: a tenant
    farmer can be owed resettlement while receiving no compensation at
    all, so a combined figure would describe nobody.
    """
    if not case_ids:
        return {
            "compensation_awarded_total": 0,
            "compensation_paid_total": 0,
            "compensation_pending_total": 0,
        }

    awarded, paid = db.query(
        func.coalesce(func.sum(Compensation.amount_awarded), 0),
        func.coalesce(func.sum(Compensation.amount_paid), 0),
    ).filter(Compensation.case_id.in_(case_ids)).one()

    return {
        "compensation_awarded_total": int(awarded),
        "compensation_paid_total": int(paid),
        "compensation_pending_total": int(awarded) - int(paid),
    }
