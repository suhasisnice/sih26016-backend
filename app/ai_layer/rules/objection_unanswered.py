from datetime import date

from app.ai_layer.constants import OBJECTION_RESPONSE_DAYS
from app.ai_layer.rules.base import Alert
from app.core.enums import AlertSeverity, ObjectionStatus

# An objection is unanswered until a decision is recorded. "under_review"
# still counts: being looked at is not the same as being responded to, and
# the statutory clock does not stop while someone reads it.
UNANSWERED_STATUSES = {ObjectionStatus.FILED.value, ObjectionStatus.UNDER_REVIEW.value}


def objection_unanswered(cases: list[dict], as_of: date) -> list[Alert]:
    """One alert per objection left unanswered past the response window.

    Always critical: an unanswered objection can invalidate the
    acquisition itself, so this carries legal weight rather than merely
    being behind schedule.

    Per objection rather than per case, because the officer needs to know
    which one to answer and a case can carry several.
    """
    alerts = []
    for case in cases:
        for objection in case["objections"]:
            if objection["status"] not in UNANSWERED_STATUSES:
                continue
            days_open = (as_of - objection["filed_on"]).days
            if days_open <= OBJECTION_RESPONSE_DAYS:
                continue
            alerts.append(
                Alert(
                    case_id=case["id"],
                    rule="objection_unanswered",
                    severity=AlertSeverity.CRITICAL,
                    message=(
                        f"Objection unanswered for {days_open} days, "
                        f"past the {OBJECTION_RESPONSE_DAYS}-day limit"
                    ),
                    detected_on=as_of,
                    details={"objection_id": objection["id"], "days_open": days_open},
                )
            )
    return alerts
