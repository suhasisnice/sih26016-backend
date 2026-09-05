"""Every tunable number and fixed decision for the AI Layer.

Nothing in the seed or the rules should hardcode a threshold, a seed value
or a district name inline — import it from here. On Day 5 these get tuned
so the demo shows a sensible spread of alerts, and hunting numbers through
five files is a bad way to spend that hour.
"""

import os
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

# The primary demo state, generated in full depth. SECONDARY_STATES exist so
# the national rollup and the state filter have more than one row to prove
# themselves against — a "nationwide" dashboard demonstrated on a single
# state demonstrates nothing.
STATE = "Karnataka"
STATE_CODE = "KA"
STATE_LGD = "29"

DISTRICT_NAMES = ["Bengaluru Rural", "Tumakuru", "Ramanagara", "Kolar"]

# (name, code, lgd_code, is_union_territory, [(district, code, lgd)])
# Real LGD codes, which is the point: they are the identifier every other
# Indian government system joins on, so carrying the real ones is what makes
# an integration possible without name matching.
SECONDARY_STATES = [
    ("Maharashtra", "MH", "27", False, [("Pune", "PUN", "521"), ("Nashik", "NSK", "516")]),
    ("Tamil Nadu", "TN", "33", False, [("Coimbatore", "CBE", "571"), ("Madurai", "MDU", "580")]),
    ("Gujarat", "GJ", "24", False, [("Surat", "SUR", "482"), ("Rajkot", "RJK", "476")]),
]

# LGD codes for the four Karnataka districts, in DISTRICT_NAMES order.
DISTRICT_LGD = {
    "Bengaluru Rural": "534",
    "Tumakuru": "544",
    "Ramanagara": "556",
    "Kolar": "538",
}

# Cases generated per district in the secondary states. Deliberately fewer
# than Karnataka: they exist to make the national view real, not to double
# the seed time.
SECONDARY_CASES_PER_DISTRICT = (3, 6)

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

# Password for every seeded demo account.
#
# The default stays readable in source on purpose: it is the password of a
# local, loopback-only database full of synthetic data, and hiding that
# protects nothing. What must NOT be the published value is the password on
# a database anyone can reach — and this repo is public, so the deployed
# seed has to be told a different one:
#
#   docker compose run --rm --no-deps -e SEED_PASSWORD=... -e DATABASE_URL=... \
#     api python -m app.ai_layer.seed --allow-remote
#
# Read from the environment rather than settings so the seed can be handed
# one without it becoming a permanent part of the app's configuration.
DEMO_PASSWORD = os.environ.get("SEED_PASSWORD") or "demo1234"

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
# Cases pushed past their stage deadline, for the timeline_breach rule. Kept
# separate from the stalled counts above: a case can be inside the flat
# ten-day stalled window and still be months past a short stage's deadline,
# and the demo needs to show that the two rules find different things.
ANOMALY_TIMELINE_BREACHED = 4

# --- Proposal pipeline ---
# Enough to show every status in the chain at once, because a pipeline
# screen with three of its seven columns empty looks broken rather than
# quiet.
PROPOSAL_COUNT_RANGE = (14, 20)

# --- Displacement ---
# Share of affected households that also lose a dwelling. Landless
# households are displaced far more often than landowners, which is the
# entire reason the Act treats the two figures separately, so the two rates
# differ here rather than one flat fraction being applied to everybody.
DISPLACED_FRACTION_LANDLESS = 0.55
DISPLACED_FRACTION_LANDOWNER = 0.18
