"""KPI 4 -- rehabilitation & resettlement progress, kept entirely separate
from compensation (see compensation.py)."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import RnRStatus
from app.models import RnRRecord

FIELDS = (
    "rnr_entitled_count",
    "rnr_pending_count",
    "rnr_in_progress_count",
    "rnr_completed_count",
    "rnr_disputed_count",
)


def rnr_kpis(db: Session, case_ids: list[int]) -> dict:
    if not case_ids:
        return {field: 0 for field in FIELDS}

    counts = {status: 0 for status in RnRStatus}
    for status, count in (
        db.query(RnRRecord.status, func.count(RnRRecord.id))
        .filter(RnRRecord.case_id.in_(case_ids))
        .group_by(RnRRecord.status)
        .all()
    ):
        counts[status] = int(count)

    return {
        "rnr_entitled_count": sum(counts.values()),
        "rnr_pending_count": counts[RnRStatus.PENDING],
        "rnr_in_progress_count": counts[RnRStatus.IN_PROGRESS],
        "rnr_completed_count": counts[RnRStatus.COMPLETED],
        "rnr_disputed_count": counts[RnRStatus.DISPUTED],
    }
