from datetime import date

from app.ai_layer.rules.base import Alert
from app.core.enums import AlertSeverity, RnRStatus, Stage

POSSESSION_STAGES = {Stage.POSSESSION.value, Stage.MONITORING.value}


def possession_before_rnr(cases: list[dict], as_of: date) -> list[Alert]:
    """Land taken while resettlement is still unfinished.

    The most domain-aware rule we have. Under the Act resettlement is meant
    to be settled before people are displaced, so this catches a sequencing
    failure with real legal weight rather than a missed deadline.
    """
    alerts = []
    for case in cases:
        if case["stage"] not in POSSESSION_STAGES:
            continue

        statuses = case["rnr_statuses"]
        incomplete = [s for s in statuses if s != RnRStatus.COMPLETED.value]

        if not statuses:
            message = "Possession taken but no R&R records exist for this case"
        elif incomplete:
            message = (
                f"Possession taken while R&R is incomplete for "
                f"{len(incomplete)} of {len(statuses)} people"
            )
        else:
            continue

        alerts.append(
            Alert(
                case_id=case["id"],
                rule="possession_before_rnr",
                severity=AlertSeverity.CRITICAL,
                message=message,
                detected_on=as_of,
                details={
                    "incomplete_rnr_count": len(incomplete),
                    "total_rnr_records": len(statuses),
                },
            )
        )
    return alerts
