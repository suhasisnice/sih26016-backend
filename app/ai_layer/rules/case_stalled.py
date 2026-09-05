"""RULE case_stalled -- a case has not moved stage in a while.

The flat threshold this rule uses is deliberately blunt (every stage judged
against the same STALLED_DAYS) -- see timeline_breach for the per-stage
deadline version. Both are kept because they answer different questions: a
case can be well inside a long stage's SLA and still be sitting still longer
than an officer would expect to see on a status list.
"""

from datetime import date

from app.ai_layer.constants import STALLED_CRITICAL_DAYS, STALLED_DAYS

RULE = "case_stalled"


def case_stalled(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        days = (as_of - case["stage_changed_at"]).days
        if days >= STALLED_CRITICAL_DAYS:
            severity = "high"
        elif days >= STALLED_DAYS:
            severity = "medium"
        else:
            continue

        alerts.append(
            {
                "case_id": case["id"],
                "rule": RULE,
                "severity": severity,
                "message": f"Stage unchanged for {days} days.",
                "detected_on": as_of.isoformat(),
                "details": {"days_stalled": days, "stage": case["stage"]},
            }
        )
    return alerts
