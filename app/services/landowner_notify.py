"""notifyLandowner — the one place a message actually goes out to a citizen
who subscribed on the public Notices page, over WhatsApp, email, or both.

Both callers — POST /notices/subscribe (an immediate "here's where your
land stands today") and POST /notices/register's issue_notice (a real
notification or declaration going out later, to everyone already
subscribed on that case's parcels) — go through this one function rather
than calling app.integrations.messaging directly. Neither knows or cares
whether a channel is mocked; that's this file's job and
app.integrations.messaging's.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import NotificationChannel, NotificationLogStatus
from app.integrations import messaging
from app.models import Case, NotificationLog, NotificationSubscription, Parcel, Project

# The three events this feature knows about — see the module docstring in
# app.models.tables.NotificationLog for why this is plain text, not an
# enum. Every caller maps whatever it actually knows (a Stage, a NoticeType)
# onto one of these three before calling notify_landowner.
PRELIMINARY_NOTIFICATION = "preliminary_notification"
DECLARATION = "declaration"
STATUS_UPDATE = "status_update"

_LABEL = {
    PRELIMINARY_NOTIFICATION: "Preliminary Notification",
    DECLARATION: "Declaration / Final Notification",
    STATUS_UPDATE: "Acquisition Status Update",
}


def _message(parcel: Parcel, project: Project | None, status_label: str, notified_on: date) -> tuple[str, str]:
    """(subject, body) — the same body for both channels, since WhatsApp
    has no separate subject line; send_email just gets one for its own use.
    Deliberately built from the actual parcel/project rows passed in, never
    a hardcoded example — see the router's own docstrings for why nothing
    downstream of a real land record should ever need placeholder data."""
    body = (
        "Dear Landowner,\n\n"
        f"Your land associated with Survey Number {parcel.survey_number} has been included "
        "in the land acquisition process.\n\n"
        f"Status: {status_label}\n"
        f"Project: {project.name if project else 'Not yet assigned'}\n"
        f"Notification Date: {notified_on.strftime('%d/%m/%Y')}\n\n"
        "Please log in to BhoomiMitra for more details."
    )
    subject = f"BhoomiMitra: {status_label} — Survey Number {parcel.survey_number}"
    return subject, body


def notify_landowner(
    db: Session,
    parcel: Parcel,
    project: Project | None,
    notification_type: str,
    status_label: str,
    notified_on: date | None = None,
) -> list[NotificationLog]:
    """Sends to every subscription on this parcel, on whichever channel(s)
    each one registered, and writes one NotificationLog row per attempt.

    Never raises — a subscriber's bad number or a provider hiccup must not
    break the request that triggered this (a citizen subscribing, or an
    officer issuing a real notice); every failure is caught, logged as
    FAILED, and returned for the caller to report or ignore as it sees fit.
    """
    subscriptions = (
        db.query(NotificationSubscription).filter(NotificationSubscription.parcel_id == parcel.id).all()
    )
    if not subscriptions:
        return []

    subject, body = _message(parcel, project, status_label, notified_on or date.today())
    provider = messaging.get_provider()
    logs: list[NotificationLog] = []

    for subscription in subscriptions:
        if subscription.whatsapp_number:
            logs.append(
                _send_one(
                    db, provider, parcel, NotificationChannel.WHATSAPP,
                    subscription.whatsapp_number, notification_type,
                    send=lambda to: provider.send_whatsapp(to, body),
                )
            )
        if subscription.email:
            logs.append(
                _send_one(
                    db, provider, parcel, NotificationChannel.EMAIL,
                    subscription.email, notification_type,
                    send=lambda to: provider.send_email(to, subject, body),
                )
            )

    db.flush()
    return logs


def _send_one(db, provider, parcel, channel, recipient, notification_type, *, send) -> NotificationLog:
    status = NotificationLogStatus.SENT
    try:
        send(recipient)
    except messaging.MessagingUnavailable:
        status = NotificationLogStatus.FAILED

    log = NotificationLog(
        parcel_id=parcel.id,
        channel=channel,
        notification_type=notification_type,
        recipient=recipient,
        status=status,
        is_mock=not provider.info.is_live,
    )
    db.add(log)
    return log


def label_for_stage(stage_value: str) -> tuple[str, str]:
    """(notification_type, status_label) for the case's CURRENT stage —
    used at subscribe time, when there's no specific event, just "here's
    where things stand today". Every stage outside the two the Act
    specifically publishes collapses to STATUS_UPDATE, labelled with its
    own name rather than a generic phrase."""
    if stage_value == "preliminary_notification":
        return PRELIMINARY_NOTIFICATION, _LABEL[PRELIMINARY_NOTIFICATION]
    if stage_value == "declaration":
        return DECLARATION, _LABEL[DECLARATION]
    return STATUS_UPDATE, stage_value.replace("_", " ").title()


def label_for_notice_type(notice_type_value: str) -> tuple[str, str]:
    """(notification_type, status_label) for a just-issued StatutoryNotice
    — used by issue_notice, which knows exactly which instrument was
    published rather than only the case's current stage."""
    if notice_type_value == "preliminary_notification":
        return PRELIMINARY_NOTIFICATION, _LABEL[PRELIMINARY_NOTIFICATION]
    if notice_type_value == "declaration":
        return DECLARATION, _LABEL[DECLARATION]
    if notice_type_value == "award":
        return STATUS_UPDATE, "Award Declared"
    return STATUS_UPDATE, "Possession Notice"
