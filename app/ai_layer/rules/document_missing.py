from datetime import date

from app.ai_layer.rules.base import Alert
from app.core.enums import AlertSeverity


def document_missing(cases: list[dict], as_of: date) -> list[Alert]:
    """A document the case's CURRENT stage requires is not on file.

    Scoped to the current stage on purpose. Flagging gaps from stages
    already passed would surface things an officer can no longer act on,
    and an alert exists to name something that can be done today.
    """
    alerts = []
    for case in cases:
        missing = sorted(set(case["required_document_types"]) - set(case["document_types"]))
        if not missing:
            continue
        alerts.append(
            Alert(
                case_id=case["id"],
                rule="document_missing",
                severity=AlertSeverity.MEDIUM,
                message=f"Missing {len(missing)} document(s) required at this stage: {', '.join(missing)}",
                detected_on=as_of,
                details={"missing_document_types": missing, "stage": case["stage"]},
            )
        )
    return alerts
