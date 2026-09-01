from datetime import date

from app.ai_layer.rules.base import Alert
from app.core.enums import AlertSeverity


def timeline_breach(cases: list[dict], as_of: date) -> list[Alert]:
    """A case that has passed the deadline for the stage it is in.

    Distinct from case_stalled, which fires on a flat number of days
    regardless of stage. This one measures against the stage's own
    allowance, so a Social Impact Assessment at day 40 is fine while a
    declaration at day 40 is late — a distinction the flat rule could not
    make and got wrong in both directions.

    Severity escalates on how far past the target it is, not on how long the
    stage has been running: a stage that is one day over is not the same
    problem as one that has doubled its allowance.
    """
    alerts = []
    for case in cases:
        due_on = case.get("stage_due_on")
        if due_on is None or as_of <= due_on:
            continue

        days_over = (as_of - due_on).days
        allowance = case.get("stage_standard_days") or 0
        # Past double the allowance the case is not late, it is abandoned.
        if allowance and days_over >= allowance:
            severity = AlertSeverity.CRITICAL
        elif allowance and days_over >= allowance * 0.5:
            severity = AlertSeverity.HIGH
        else:
            severity = AlertSeverity.MEDIUM

        alerts.append(
            Alert(
                case_id=case["id"],
                rule="timeline_breach",
                severity=severity,
                message=(
                    f"Stage deadline passed {days_over} day(s) ago "
                    f"(due {due_on.isoformat()})"
                ),
                detected_on=as_of,
                details={
                    "days_over": days_over,
                    "due_on": due_on.isoformat(),
                    "stage": case["stage"],
                    "standard_days": allowance,
                },
            )
        )
    return alerts
