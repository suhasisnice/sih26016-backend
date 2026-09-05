"""Rule-based alerts: what is wrong with a case right now.

Six independent, pure functions of `(cases, as_of)`. Each takes the plain
dicts `app.ai_layer.loaders.load_cases` builds and returns a list of alert
dicts in the shape `app.services.alerts.regenerate_alerts` persists. None of
them touch the database or print — that is what keeps them independently
testable and safe to run in any order.

`run_all_rules` isolates failures: one rule raising must not blank out the
other five, so each runs in its own try/except and a failure is logged
rather than propagated.
"""

import logging
from datetime import date

from app.ai_layer.rules.award_unpaid import award_unpaid
from app.ai_layer.rules.case_stalled import case_stalled
from app.ai_layer.rules.document_missing import document_missing
from app.ai_layer.rules.fund_deposit_missing import fund_deposit_missing
from app.ai_layer.rules.objection_unanswered import objection_unanswered
from app.ai_layer.rules.possession_before_rnr import possession_before_rnr
from app.ai_layer.rules.timeline_breach import timeline_breach
from app.ai_layer.rules.unused_land import unused_land

logger = logging.getLogger(__name__)

ALL_RULES = (
    case_stalled,
    document_missing,
    objection_unanswered,
    award_unpaid,
    possession_before_rnr,
    timeline_breach,
    fund_deposit_missing,
    unused_land,
)

__all__ = ["run_all_rules", "ALL_RULES"]


def run_all_rules(cases: list[dict], as_of: date) -> list[dict]:
    """Run every rule over the given cases, worst-isolated.

    Same input, same output, every time -- these rules are deterministic and
    have no side effects, which is what lets `regenerate_alerts` rebuild the
    entire alerts table from scratch on every run.
    """
    produced: list[dict] = []
    for rule in ALL_RULES:
        try:
            produced.extend(rule(cases, as_of))
        except Exception:
            logger.exception("Rule %s failed; other rules still ran.", rule.__name__)
    return produced
