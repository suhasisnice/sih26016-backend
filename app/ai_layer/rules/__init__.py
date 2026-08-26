"""Rule registry and runner.

Every rule has the signature (cases: list[dict], as_of: date) -> list[Alert]
and is pure: data in, alerts out. A rule never touches the database, never
prints, and takes "today" as an argument rather than reading the clock
itself, so the same input always produces the same output.

The runner is the only thing that calls the rules, and app.services.alerts
is the only thing that writes what they return.
"""

import logging
from datetime import date

from app.ai_layer.rules.award_unpaid import award_unpaid
from app.ai_layer.rules.case_stalled import case_stalled
from app.ai_layer.rules.document_missing import document_missing
from app.ai_layer.rules.objection_unanswered import objection_unanswered
from app.ai_layer.rules.possession_before_rnr import possession_before_rnr

logger = logging.getLogger(__name__)

REGISTRY = {
    "case_stalled": case_stalled,
    "document_missing": document_missing,
    "objection_unanswered": objection_unanswered,
    "award_unpaid": award_unpaid,
    "possession_before_rnr": possession_before_rnr,
}


def run_all_rules(cases: list[dict], as_of: date) -> list[dict]:
    """Run every registered rule and return a flat list of alert dicts.

    A rule that raises is logged and skipped so the others still run. A
    broken rule must never take the dashboard down mid-demo — an
    incomplete alert list is recoverable, a 500 on the front page is not.
    """
    alerts: list[dict] = []
    for rule_name, rule_fn in REGISTRY.items():
        try:
            alerts.extend(alert.to_dict() for alert in rule_fn(cases, as_of))
        except Exception:  # noqa: BLE001 — isolate any single rule's failure
            logger.exception("rule '%s' failed and was skipped", rule_name)
    return alerts
