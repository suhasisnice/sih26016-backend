"""RULE objection_unanswered -- an open objection has sat unanswered too
long.

Always critical: an objection under RFCTLARR is a statutory right, and one
left unresolved past a reasonable response window can invalidate the
acquisition it was filed against. This is the alert with the most legal
weight in the set.
"""

from datetime import date

from app.ai_layer.constants import OBJECTION_RESPONSE_DAYS

RULE = "objection_unanswered"

OPEN_STATUSES = {"filed", "under_review"}


def objection_unanswered(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        for objection in case["objections"]:
            if objection["status"] not in OPEN_STATUSES:
                continue
            days_open = (as_of - objection["filed_on"]).days
            if days_open <= OBJECTION_RESPONSE_DAYS:
                continue

            alerts.append(
                {
                    "case_id": case["id"],
                    "rule": RULE,
                    "severity": "critical",
                    "message": f"Objection open {days_open} days with no response.",
                    "detected_on": as_of.isoformat(),
                    "details": {
                        "objection_id": objection["id"],
                        "days_open": days_open,
                    },
                }
            )
    return alerts
