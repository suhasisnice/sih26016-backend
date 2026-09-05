"""RULE document_missing -- a document required by the case's current stage
is not on file.

Reads `required_document_types` from the loader rather than a constant in
this module, so the lookup Backend owns (the `required_documents` table)
stays the single source of truth for what each stage needs.
"""

from datetime import date

RULE = "document_missing"


def document_missing(cases: list[dict], as_of: date) -> list[dict]:
    alerts = []
    for case in cases:
        required = set(case["required_document_types"])
        if not required:
            continue
        missing = sorted(required - set(case["document_types"]))
        if not missing:
            continue

        alerts.append(
            {
                "case_id": case["id"],
                "rule": RULE,
                "severity": "medium",
                "message": (
                    f"{len(missing)} document(s) required for "
                    f"{case['stage'].replace('_', ' ')} not on file."
                ),
                "detected_on": as_of.isoformat(),
                "details": {"missing_document_types": missing},
            }
        )
    return alerts
