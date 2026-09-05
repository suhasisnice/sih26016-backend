"""KPI 2 -- compensation awarded, paid and pending, in whole rupees.

Never merged with the R&R figures in rnr.py -- compensation is money for
land taken, R&R is entitlements for displacement, and a person can be owed
one without the other.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Compensation


def compensation_kpis(db: Session, case_ids: list[int]) -> dict:
    if not case_ids:
        return {
            "compensation_awarded_total": 0,
            "compensation_paid_total": 0,
            "compensation_pending_total": 0,
        }

    awarded, paid = (
        db.query(
            func.coalesce(func.sum(Compensation.amount_awarded), 0),
            func.coalesce(func.sum(Compensation.amount_paid), 0),
        )
        .filter(Compensation.case_id.in_(case_ids))
        .one()
    )
    awarded, paid = int(awarded), int(paid)
    return {
        "compensation_awarded_total": awarded,
        "compensation_paid_total": paid,
        "compensation_pending_total": max(0, awarded - paid),
    }
