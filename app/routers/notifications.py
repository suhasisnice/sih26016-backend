"""The per-user inbox.

An alert on the dashboard is only seen by somebody who opens the dashboard.
The problem statement asks for "automated alerts and notifications", and the
second half of that phrase means something arrives, addressed to a person,
and stays until they deal with it.

Every route here is scoped to the calling user's own rows. There is no
"read someone else's inbox" endpoint, for admins or anyone else: a
notification is addressed correspondence, and the audit trail already covers
the "who knew what" question an administrator might reasonably have.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity
from app.dependencies import get_current_user, get_db
from app.models import Case, Notification, User
from app.schemas.notification import (
    MarkReadRequest,
    MarkReadResult,
    NotificationList,
    NotificationOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _unread_count(db: Session, user: User) -> int:
    return (
        db.query(func.count(Notification.id))
        .filter(Notification.user_id == user.id, Notification.is_read.is_(False))
        .scalar()
        or 0
    )


@router.get("", response_model=NotificationList)
def list_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    unread_only: bool = False,
    severity: AlertSeverity | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """This user's notifications, newest first.

    Newest-first here, unlike the case list's oldest-first: an inbox is read
    as a feed of what has happened since you last looked, while a work queue
    is read as what has been waiting longest. Different question, different
    order.
    """
    query = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    if severity is not None:
        query = query.filter(Notification.severity == severity)

    total = query.order_by(None).count()
    rows = (
        query.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    # Case numbers in one lookup rather than one per row.
    case_ids = {row.case_id for row in rows if row.case_id is not None}
    case_numbers = (
        dict(db.query(Case.id, Case.case_number).filter(Case.id.in_(case_ids)).all())
        if case_ids
        else {}
    )

    return NotificationList(
        items=[
            NotificationOut(
                id=row.id,
                case_id=row.case_id,
                case_number=case_numbers.get(row.case_id),
                rule=row.rule,
                severity=row.severity,
                title=row.title,
                body=row.body,
                is_read=row.is_read,
                created_at=row.created_at,
                details=row.details or {},
            )
            for row in rows
        ],
        total=total,
        unread_count=_unread_count(db, user),
    )


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Just the badge number.

    A separate route because the nav polls this and does not need the
    payload — returning fifty rows to render one integer is the kind of
    thing that looks free until a thousand people have the app open.
    """
    return {"unread_count": _unread_count(db, user)}


@router.post("/mark-read", response_model=MarkReadResult)
def mark_read(
    payload: MarkReadRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark specific notifications read, or all of them.

    The user_id filter is applied to the UPDATE itself, not checked
    beforehand — so a caller who sends somebody else's notification ids
    marks nothing, rather than being told whose they are.
    """
    query = db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read.is_(False)
    )
    if payload.notification_ids is not None:
        if not payload.notification_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="notification_ids was empty. Omit it entirely to mark everything read.",
            )
        query = query.filter(Notification.id.in_(payload.notification_ids))

    marked = query.update({Notification.is_read: True}, synchronize_session=False)
    db.commit()

    return MarkReadResult(marked=marked, unread_count=_unread_count(db, user))
