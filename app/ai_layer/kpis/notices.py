"""KPI 7 -- published statutory instruments.

Counts `statutory_notices` rows rather than inferring from a case's current
stage, so a case that has moved past declaration still counts as having
been notified -- a cumulative figure that fell as work progressed would be
worse than no figure at all.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import NoticeType
from app.models import StatutoryNotice


def notices_kpis(db: Session, case_ids: list[int]) -> dict:
    if not case_ids:
        return {
            "notifications_issued_count": 0,
            "declarations_issued_count": 0,
            "awards_declared_count": 0,
            "possession_notices_count": 0,
            "awards_declared_amount": 0,
        }

    counts = {notice_type: 0 for notice_type in NoticeType}
    for notice_type, count in (
        db.query(StatutoryNotice.notice_type, func.count(StatutoryNotice.id))
        .filter(StatutoryNotice.case_id.in_(case_ids))
        .group_by(StatutoryNotice.notice_type)
        .all()
    ):
        counts[notice_type] = int(count)

    award_amount = (
        db.query(func.coalesce(func.sum(StatutoryNotice.total_amount), 0))
        .filter(
            StatutoryNotice.case_id.in_(case_ids),
            StatutoryNotice.notice_type == NoticeType.AWARD,
        )
        .scalar()
    )

    return {
        "notifications_issued_count": counts[NoticeType.PRELIMINARY_NOTIFICATION],
        "declarations_issued_count": counts[NoticeType.DECLARATION],
        "awards_declared_count": counts[NoticeType.AWARD],
        "possession_notices_count": counts[NoticeType.POSSESSION_NOTICE],
        "awards_declared_amount": int(award_amount or 0),
    }
