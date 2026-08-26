from datetime import date

from app.ai_layer.constants import AWARD_PAYMENT_DAYS
from app.ai_layer.rules.base import Alert
from app.core.enums import AlertSeverity, CompensationStatus

# Awarded or assessed but not yet paid. PAID is settled; DISPUTED is a
# different problem with its own process, and chasing it as a late payment
# would be wrong — the money is held back deliberately.
UNPAID_STATUSES = {CompensationStatus.AWARDED.value, CompensationStatus.ASSESSED.value}


def award_unpaid(cases: list[dict], as_of: date) -> list[Alert]:
    """Compensation decided but still not disbursed past the payment window.

    Aggregated to one alert per case, unlike objection_unanswered: the
    action is to chase one case's disbursement, and a beneficiary count
    plus an outstanding total convey the urgency without putting anybody's
    name on a dashboard.
    """
    alerts = []
    for case in cases:
        overdue = [
            comp
            for comp in case["compensations"]
            if comp["status"] in UNPAID_STATUSES
            and comp["awarded_on"] is not None
            and (as_of - comp["awarded_on"]).days > AWARD_PAYMENT_DAYS
        ]
        if not overdue:
            continue
        amount_pending = sum(comp["amount_awarded"] - comp["amount_paid"] for comp in overdue)
        longest_wait = max((as_of - comp["awarded_on"]).days for comp in overdue)
        alerts.append(
            Alert(
                case_id=case["id"],
                rule="award_unpaid",
                severity=AlertSeverity.HIGH,
                message=(
                    f"Award unpaid for {len(overdue)} beneficiary(ies) after {longest_wait} days, "
                    f"Rs {amount_pending:,} outstanding"
                ),
                detected_on=as_of,
                details={
                    "beneficiary_count": len(overdue),
                    "amount_pending": amount_pending,
                    "days_since_award": longest_wait,
                },
            )
        )
    return alerts
