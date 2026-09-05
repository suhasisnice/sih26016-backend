"""RULE fund_deposit_missing -- the award has been passed but the requiring
body has deposited nothing against the case.

Sits between award_unpaid and the possession stage in the real lifecycle:
disbursement cannot happen before the money exists to disburse. This is the
finding for "nothing has moved yet" as distinct from award_unpaid's "some
money moved but not enough, and the clock has run out" -- catching a stall
here is catching it earlier, before the 30-day payment window even starts.
"""

from datetime import date

from app.core.enums import Stage

RULE = "fund_deposit_missing"

STAGE_ORDER = list(Stage)
AWARD_INDEX = STAGE_ORDER.index(Stage.AWARD)


def fund_deposit_missing(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        if STAGE_ORDER.index(Stage(case["stage"])) < AWARD_INDEX:
            continue
        if case["fund_deposited"]:
            continue

        alerts.append(
            {
                "case_id": case["id"],
                "rule": RULE,
                "severity": "high",
                "message": "Award passed but the requiring body has deposited no funds against this case.",
                "detected_on": as_of.isoformat(),
                "details": {},
            }
        )
    return alerts
