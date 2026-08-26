from datetime import date

from app.ai_layer.constants import STALLED_CRITICAL_DAYS, STALLED_DAYS
from app.ai_layer.rules.base import Alert
from app.core.enums import AlertSeverity


def case_stalled(cases: list[dict], as_of: date) -> list[Alert]:
    """A case whose stage has not moved in STALLED_DAYS or more.

    The single most useful rule we have: LACRRIS records what happened,
    and this is the thing that notices what has stopped happening.
    """
    alerts = []
    for case in cases:
        days_stalled = (as_of - case["stage_changed_at"]).days
        if days_stalled < STALLED_DAYS:
            continue
        severity = (
            AlertSeverity.CRITICAL if days_stalled >= STALLED_CRITICAL_DAYS else AlertSeverity.HIGH
        )
        alerts.append(
            Alert(
                case_id=case["id"],
                rule="case_stalled",
                severity=severity,
                message=f"Stage unchanged for {days_stalled} days",
                detected_on=as_of,
                details={"days_stalled": days_stalled, "stage": case["stage"]},
            )
        )
    return alerts
