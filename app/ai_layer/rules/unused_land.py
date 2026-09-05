"""RULE unused_land -- possession was taken more than five years ago and
the case is still sitting open.

Sec. 101: land acquired but left unutilised for five years reverts to a
land bank. There is no "utilised" flag anywhere in the schema -- nothing
records that a highway got built on the parcel -- so a case still open
(not CLOSED, which the workflow only sets on reaching the terminal
MONITORING stage) five years after possession is the honest proxy: it
means nobody has recorded the acquisition as wrapped up.
"""

from datetime import date, timedelta

RULE = "unused_land"

FIVE_YEARS_DAYS = 5 * 365


def unused_land(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        possession_since = case["possession_since"]
        if possession_since is None or case["status"] == "closed":
            continue

        years_since = (as_of - possession_since).days
        if years_since <= FIVE_YEARS_DAYS:
            continue

        alerts.append(
            {
                "case_id": case["id"],
                "rule": RULE,
                "severity": "high",
                "message": (
                    f"Possession taken {years_since // 365} years ago; "
                    "no record the land has been utilised."
                ),
                "detected_on": as_of.isoformat(),
                "details": {
                    "possession_since": possession_since.isoformat(),
                    "days_since_possession": years_since,
                },
            }
        )
    return alerts
