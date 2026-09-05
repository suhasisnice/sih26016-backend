"""RULE award_unpaid -- compensation was awarded but has not been fully
paid within AWARD_PAYMENT_DAYS.

Aggregated to one alert per case rather than one per person: an officer
looking at a case list needs to know the case has a payment problem, and the
per-person amounts are in `details` for whoever opens it.
"""

from datetime import date

from app.ai_layer.constants import AWARD_PAYMENT_DAYS

RULE = "award_unpaid"


def award_unpaid(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        unpaid = [
            comp
            for comp in case["compensations"]
            if comp["awarded_on"] is not None
            and comp["amount_paid"] < comp["amount_awarded"]
            and (as_of - comp["awarded_on"]).days > AWARD_PAYMENT_DAYS
        ]
        if not unpaid:
            continue

        amount_pending = sum(comp["amount_awarded"] - comp["amount_paid"] for comp in unpaid)
        alerts.append(
            {
                "case_id": case["id"],
                "rule": RULE,
                "severity": "medium",
                "message": (
                    f"Compensation for {len(unpaid)} beneficiary(ies) still unpaid "
                    f"more than {AWARD_PAYMENT_DAYS} days after award."
                ),
                "detected_on": as_of.isoformat(),
                "details": {
                    "beneficiary_count": len(unpaid),
                    "amount_pending": amount_pending,
                },
            }
        )
    return alerts
