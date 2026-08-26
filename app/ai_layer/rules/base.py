"""The shape every rule returns.

Severity uses app.core.enums.AlertSeverity, the same four values Frontend
reads from /meta/enums. Our own working vocabulary was warning|critical;
those map onto HIGH and CRITICAL here so there is one severity scale in
the system rather than two that need translating at the boundary.

How the five rules use the scale:
  MEDIUM   — paperwork is behind (document_missing)
  HIGH     — someone is waiting on the state (case_stalled, award_unpaid)
  CRITICAL — legal exposure (objection_unanswered, possession_before_rnr,
             and a case stalled past the critical threshold)
LOW is unused today; it exists for rules added later.
"""

from dataclasses import dataclass, field
from datetime import date

from app.core.enums import AlertSeverity


@dataclass
class Alert:
    case_id: int
    rule: str
    severity: AlertSeverity
    message: str
    detected_on: date
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Plain JSON-safe dict, matching the published alert contract."""
        return {
            "case_id": self.case_id,
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "detected_on": self.detected_on.isoformat(),
            "details": self.details,
        }
