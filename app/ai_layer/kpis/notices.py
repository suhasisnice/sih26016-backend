"""KPI 7 - notifications issued and awards declared.

Two of the parameters the problem statement names as live figures. Both used
to be inferred from a case's CURRENT stage, which quietly made them wrong:
a case that moved past declaration stopped counting as ever having been
notified, so the cumulative total went DOWN as work progressed.

Counting published instruments instead fixes that. A statutory notice is
issued once and the row stays forever, so these numbers only ever rise, and
each one traces to a gazette reference rather than to an inference.
"""

from sqlalchemy import func

from app.core.enums import NoticeType
from app.models import StatutoryNotice


def compute_notices(db, case_ids: list[int]) -> dict:
    empty = {
        "notifications_issued_count": 0,
        "declarations_issued_count": 0,
        "awards_declared_count": 0,
        "possession_notices_count": 0,
        "awards_declared_amount": 0,
    }
    if not case_ids:
        return empty

    counts = {
        notice_type: count
        for notice_type, count in db.query(
            StatutoryNotice.notice_type, func.count(StatutoryNotice.id)
        )
        .filter(StatutoryNotice.case_id.in_(case_ids))
        .group_by(StatutoryNotice.notice_type)
        .all()
    }

    # The money committed at declaration, which is fixed by the award and
    # does not move as payments are made. Deliberately NOT the same as
    # compensation_awarded_total: that one is summed from live compensation
    # rows and can change if an award is revised. Reporting both lets a
    # discrepancy be seen rather than averaged away.
    awarded_amount = (
        db.query(func.coalesce(func.sum(StatutoryNotice.total_amount), 0))
        .filter(
            StatutoryNotice.case_id.in_(case_ids),
            StatutoryNotice.notice_type == NoticeType.AWARD,
        )
        .scalar()
        or 0
    )

    return {
        "notifications_issued_count": int(counts.get(NoticeType.PRELIMINARY_NOTIFICATION, 0)),
        "declarations_issued_count": int(counts.get(NoticeType.DECLARATION, 0)),
        "awards_declared_count": int(counts.get(NoticeType.AWARD, 0)),
        "possession_notices_count": int(counts.get(NoticeType.POSSESSION_NOTICE, 0)),
        "awards_declared_amount": int(awarded_amount),
    }
