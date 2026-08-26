"""Runs the AI Layer's rules and persists what they find.

The rules themselves never touch the database — this is the only place
their output is written, which is what keeps them pure and independently
testable.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.ai_layer.loaders import load_cases
from app.ai_layer.rules import run_all_rules
from app.core.enums import AlertSeverity
from app.models import Alert

SEVERITY_ORDER = {
    AlertSeverity.CRITICAL: 0,
    AlertSeverity.HIGH: 1,
    AlertSeverity.MEDIUM: 2,
    AlertSeverity.LOW: 3,
}


def regenerate_alerts(db: Session, as_of: date | None = None) -> dict:
    """Recompute every alert from scratch and replace the table's contents.

    Full replacement rather than an incremental update, deliberately: an
    alert exists only while the condition that produced it is still true,
    so a case that has since moved should stop being flagged without
    anyone having to remember to clear it. The rules are pure functions of
    the current data, which makes the table safe to rebuild at any time.

    Does not commit — the caller owns the transaction.
    """
    as_of = as_of or date.today()

    cases = load_cases(db)
    produced = run_all_rules(cases, as_of)

    db.query(Alert).delete()
    for entry in produced:
        db.add(
            Alert(
                case_id=entry["case_id"],
                rule=entry["rule"],
                severity=AlertSeverity(entry["severity"]),
                message=entry["message"],
                detected_on=date.fromisoformat(entry["detected_on"]),
                details=entry["details"],
                is_resolved=False,
            )
        )

    by_rule: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for entry in produced:
        by_rule[entry["rule"]] = by_rule.get(entry["rule"], 0) + 1
        by_severity[entry["severity"]] = by_severity.get(entry["severity"], 0) + 1

    return {
        "cases_evaluated": len(cases),
        "alerts_generated": len(produced),
        "by_rule": by_rule,
        "by_severity": by_severity,
        "as_of": as_of.isoformat(),
    }
