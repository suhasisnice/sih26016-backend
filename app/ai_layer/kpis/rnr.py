from sqlalchemy import func

from app.core.enums import RnRStatus
from app.models import RnRRecord


def compute_rnr(db, case_ids: list[int]) -> dict:
    """KPI 4 - rehabilitation and resettlement progress, counted in people.

    Kept entirely apart from compensation. This measures housing and
    livelihood support for displaced households; compensation.py measures
    money for land. Never add the two, and never report one as a proxy
    for the other.
    """
    counts = {status: 0 for status in RnRStatus}
    if case_ids:
        rows = (
            db.query(RnRRecord.status, func.count())
            .filter(RnRRecord.case_id.in_(case_ids))
            .group_by(RnRRecord.status)
            .all()
        )
        for status, count in rows:
            counts[status] = count

    # The four status counts are mutually exclusive buckets, so the total is
    # their sum. It is returned explicitly rather than left to the caller:
    # the field was previously named rnr_entitled_count while holding only
    # the PENDING count, which read as "everyone entitled" and produced
    # "9 completed of 2 entitled" on the dashboard.
    return {
        "rnr_pending_count": counts[RnRStatus.PENDING],
        "rnr_in_progress_count": counts[RnRStatus.IN_PROGRESS],
        "rnr_completed_count": counts[RnRStatus.COMPLETED],
        "rnr_disputed_count": counts[RnRStatus.DISPUTED],
        "rnr_entitled_count": sum(counts.values()),
    }
