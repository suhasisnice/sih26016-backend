"""The per-user inbox contract.

Separate from AlertOut on purpose. An alert is a finding about a case and is
the same for everyone who can see that case; a notification is that finding
delivered to one person, with their own read state on it.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AlertSeverity


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int | None
    case_number: str | None = None
    rule: str | None
    severity: AlertSeverity
    title: str
    body: str
    is_read: bool
    created_at: datetime
    details: dict = Field(default_factory=dict)


class NotificationList(BaseModel):
    items: list[NotificationOut]
    total: int
    # Drives the badge on the nav. Returned with every page so the count
    # cannot drift from the list the user is looking at.
    unread_count: int


class MarkReadRequest(BaseModel):
    """Mark specific notifications read, or all of them.

    Explicit ids rather than "everything before timestamp X": a user who
    marks all as read while a rule run is committing should not silently
    dismiss findings they never saw.
    """

    notification_ids: list[int] | None = Field(
        default=None,
        max_length=500,
        description="Ids to mark read. Omit to mark every unread notification read.",
    )


class MarkReadResult(BaseModel):
    marked: int
    unread_count: int
