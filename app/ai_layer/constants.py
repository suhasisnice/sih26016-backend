"""Every tunable number and fixed decision for the AI Layer.

Nothing in the seed or the rules should hardcode a threshold, a seed value
or a district name inline — import it from here. On Day 5 these get tuned
so the demo shows a sensible spread of alerts, and hunting numbers through
five files is a bad way to spend that hour.
"""

from datetime import date

# The date the generated data is built around — every "days ago" in the
# seed is measured back from this.
#
# It follows the real clock by default, and that is deliberate. Pinning it
# to a fixed date means the data ages while the rules keep reading
# date.today(): one day after the pin the stalled-case count goes from 4 to
# 9, and ten days later all 52 cases are flagged and the dashboard is a
# wall of red. Reseeding always produces the same structure — same ids,
# same case numbers, same alert counts — with the dates slid to match
# whatever today is.
#
# Set SEED_ANCHOR_DATE to a real date only to reproduce an exact past run,
# and expect the alert counts to drift if you then leave it pinned.
SEED_ANCHOR_DATE: date | None = None


def anchor_date() -> date:
    return SEED_ANCHOR_DATE or date.today()

# --- Alert rule thresholds ---
STALLED_DAYS = 10
STALLED_CRITICAL_DAYS = 20
OBJECTION_RESPONSE_DAYS = 21
AWARD_PAYMENT_DAYS = 30

# --- Sample data ---
RANDOM_SEED = 26016

STATE = "Karnataka"
DISTRICT_NAMES = ["Bengaluru Rural", "Tumakuru", "Ramanagara", "Kolar"]

CASE_COUNT_RANGE = (40, 60)
PARCEL_COUNT_RANGE = (200, 300)
PERSON_COUNT_MIN = 300
OBJECTION_COUNT_RANGE = (15, 25)

LANDLESS_AFFECTED_FRACTION = 0.3

PARCEL_AREA_HA_RANGE = (0.05, 2.5)
COMPENSATION_RATE_PER_HA_RANGE = (1_500_000, 3_500_000)

# Phone numbers are issued sequentially from one obviously-fake block
# rather than sampled from the real Indian mobile range. A random 10-digit
# 9-series number is very likely to be somebody's actual number, and these
# end up on screens, in screenshots and in the deck.
FAKE_PHONE_PREFIX = "99999"

# Password for every seeded demo account. Fine only because this stack is
# loopback-only with synthetic data; never reuse it anywhere real.
DEMO_PASSWORD = "demo1234"

# --- Deliberate anomalies ---
# The baseline data is generated so it never trips a rule by accident, so
# these counts are exactly what the dashboard will show.
ANOMALY_FRACTION = 0.15
ANOMALY_STALLED_CRITICAL_CASES = 2
ANOMALY_STALLED_WARNING_CASES = 2
ANOMALY_DOCUMENTS_REMOVED = 3
ANOMALY_OBJECTIONS_FORCED_OPEN = 2
ANOMALY_AWARDS_FORCED_UNPAID = 1
ANOMALY_POSSESSION_BEFORE_RNR = 1
