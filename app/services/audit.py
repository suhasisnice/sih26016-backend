"""The audit trail.

The problem statement asks for a permanent record of who did what and when,
so every mutating route calls record() before returning. Append-only: no
route in this API updates or deletes an audit row.

`detail` is a short human-readable summary. Keep personal data out of it —
audit rows are read by admins reviewing activity, and a name or phone
number written here is personal data copied somewhere nobody is watching.
Reference entities by id.
"""

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(
    db: Session,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    detail: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
    )
    db.add(entry)
    # Deliberately not committed here. The caller commits, so the audit row
    # and the change it describes land in the same transaction — an action
    # can never be recorded for a write that then failed, and a successful
    # write can never go unrecorded.
    return entry
