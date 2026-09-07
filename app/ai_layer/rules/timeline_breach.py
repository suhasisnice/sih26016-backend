"""RULE timeline_breach -- a case is past its current stage's own deadline.

Distinct from case_stalled: that rule applies one flat threshold to every
stage, while this one reads `stage_due_on`, which `app.services.sla` derives
per stage from `stage_sla`. A case can be well inside the ten-day stalled
window and still be months overdue on a short stage's deadline, and the two
rules are kept separate so the demo can show that they catch different
things.
"""

from datetime import date

RULE = "timeline_breach"


def timeline_breach(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        # Same reasoning as case_stalled: a closed case has no next
        # milestone to miss, and its final stage's deadline going by is not
        # a breach of anything. Rules that describe an OUTCOME rather than
        # momentum — an unpaid award, an unanswered objection, a missing
        # fund deposit — deliberately keep firing on closed cases, because
        # closing a case does not settle any of them.
        if case["status"] == "closed":
            continue

        due_on = case["stage_due_on"]
        if due_on is None or as_of <= due_on:
            continue

        days_overdue = (as_of - due_on).days
        alerts.append(
            {
                "case_id": case["id"],
                "rule": RULE,
                "severity": "high",
                "message": f"Stage deadline missed by {days_overdue} days.",
                "detected_on": as_of.isoformat(),
                "details": {
                    "days_overdue": days_overdue,
                    "stage_due_on": due_on.isoformat(),
                },
            }
        )
    return alerts
