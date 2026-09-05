"""RULE possession_before_rnr -- a case has reached possession while
rehabilitation & resettlement is not yet complete.

Always critical. This is the most domain-aware rule in the set: the Act
intends R&R entitlements to be settled BEFORE a family is displaced, so a
case sitting at or past the possession stage with outstanding R&R records is
the process running in the wrong order, not just running late.

The loader reports case stage rather than per-parcel possession status, so
"reached possession" is read from the case having advanced to the possession
stage or beyond -- the same granularity every other rule in this set works
at.
"""

from datetime import date

from app.core.enums import Stage

RULE = "possession_before_rnr"

STAGE_ORDER = list(Stage)
POSSESSION_INDEX = STAGE_ORDER.index(Stage.POSSESSION)
INCOMPLETE_STATUSES = {"pending", "in_progress", "disputed"}


def possession_before_rnr(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        if STAGE_ORDER.index(Stage(case["stage"])) < POSSESSION_INDEX:
            continue

        statuses = case["rnr_statuses"]
        if not statuses:
            continue
        outstanding = sum(1 for status in statuses if status in INCOMPLETE_STATUSES)
        if outstanding == 0:
            continue

        alerts.append(
            {
                "case_id": case["id"],
                "rule": RULE,
                "severity": "critical",
                "message": (
                    f"Possession stage reached with {outstanding} R&R "
                    "entitlement(s) not yet completed."
                ),
                "detected_on": as_of.isoformat(),
                "details": {"rnr_outstanding_count": outstanding},
            }
        )
    return alerts
