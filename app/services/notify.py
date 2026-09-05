"""Turning findings into things that reach a person.

An Alert is a finding about a case. A Notification is that finding put in
front of somebody who can act on it. Keeping them separate is what lets the
alerts table be rebuilt from scratch on every rule run — which is how the
rules are designed to work — without blanking out everybody's inbox.

Who gets what is decided here, once, rather than in each rule:

- Every rule goes to the officers responsible for that case's district.
- Compensation findings additionally reach the SLAO, R&R findings the R&R
  officer, because those are the offices that can actually clear them.
- A landowner is never notified about an internal finding raised by the
  rule engine. An alert says the state is behind on its own work; sending
  that to the affected family would be alarming without being actionable,
  and alert details are written for officers, not for the public.
- A landowner IS notified about their own case moving — an objection
  answered, a stage advanced — through notify_case_landowners and
  notify_objection_filer below. That is a different thing from an alert:
  it is a fact about their case, not a finding about the office's
  performance, and it is the reason the "does severity matter to a
  landowner" question had a real answer to build toward.

Fan-out is deliberately bounded: notifications go to district-scoped
officers and state officers for the state in question, never to every admin
in the country, or a national rule run would write one row per admin per
case and the inbox would be unusable on the first night.
"""

from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity, Role
from app.models import Alert, Case, Notification, Parcel, User

# Which roles care about which rule. A rule not listed here goes to the
# general set — better to over-notify a district officer than to drop a
# finding because nobody remembered to register its rule.
GENERAL_RECIPIENT_ROLES = (Role.DISTRICT_OFFICER, Role.SLAO)

RULE_EXTRA_ROLES: dict[str, tuple[Role, ...]] = {
    "award_unpaid": (Role.SLAO,),
    "possession_before_rnr": (Role.RNR_OFFICER,),
    "rnr_stalled": (Role.RNR_OFFICER,),
    "document_missing": (Role.FIELD_OFFICER,),
    "timeline_breach": (Role.DISTRICT_OFFICER,),
}

# Titles read by a human in an inbox, so they name the problem rather than
# the rule that found it.
RULE_TITLES = {
    "case_stalled": "Case has not moved",
    "document_missing": "Required document missing",
    "objection_unanswered": "Objection past its response deadline",
    "award_unpaid": "Award declared but not disbursed",
    "possession_before_rnr": "Possession taken before R&R completed",
    "timeline_breach": "Stage deadline passed",
}

# Below this, a finding shows on the dashboard but does not generate an
# inbox item. A notification that is not worth interrupting somebody for
# should not be a notification.
MIN_SEVERITY_FOR_NOTIFICATION = (
    AlertSeverity.MEDIUM,
    AlertSeverity.HIGH,
    AlertSeverity.CRITICAL,
)


def recipients_for(db: Session, case: Case, rule: str) -> list[User]:
    """Officers who should see this finding about this case.

    Scoped to the case's own district, plus any state officer for the state
    it sits in. Admins are excluded on purpose: they see everything on the
    dashboard already, and a national account does not want an inbox item
    for every district's paperwork.
    """
    roles = set(GENERAL_RECIPIENT_ROLES) | set(RULE_EXTRA_ROLES.get(rule, ()))

    district_officers = (
        db.query(User)
        .filter(
            User.is_active.is_(True),
            User.district_id == case.district_id,
            User.role.in_(tuple(roles)),
        )
        .all()
    )

    # State officers for the state this case actually sits in. Filtered in
    # SQL rather than loaded and sifted in Python, so a national deployment
    # does not pull every state officer in the country per case.
    state_id = case.district.state_id if case.district else None
    state_officers = (
        db.query(User)
        .filter(
            User.is_active.is_(True),
            User.role == Role.STATE_OFFICER,
            User.state_id == state_id,
        )
        .all()
        if state_id is not None
        else []
    )

    by_id = {u.id: u for u in district_officers + state_officers}
    return list(by_id.values())


def fan_out(db: Session, limit_per_run: int = 2000) -> dict:
    """Create inbox items for every unresolved alert that does not already
    have one open.

    Idempotent by construction: a partial unique index allows at most one
    UNREAD notification per user per case per rule, and this checks the same
    condition before inserting, so running the rules twice in one night does
    not hand anybody a duplicate. Read notifications are excluded from that
    constraint, so a finding somebody dismissed can legitimately be raised
    again later.

    Does not commit — the caller owns the transaction.
    """
    alerts = (
        db.query(Alert)
        .filter(Alert.is_resolved.is_(False))
        .filter(Alert.severity.in_(MIN_SEVERITY_FOR_NOTIFICATION))
        .order_by(Alert.case_id, Alert.id)
        .limit(limit_per_run)
        .all()
    )
    if not alerts:
        return {"notifications_created": 0, "recipients": 0, "alerts_considered": 0}

    case_ids = {alert.case_id for alert in alerts}
    cases = {c.id: c for c in db.query(Case).filter(Case.id.in_(case_ids)).all()}

    # One query for everything already sitting unread, rather than one
    # existence check per alert per recipient.
    already_open = {
        (user_id, case_id, rule)
        for user_id, case_id, rule in db.query(
            Notification.user_id, Notification.case_id, Notification.rule
        ).filter(Notification.is_read.is_(False))
    }

    created = 0
    touched_users: set[int] = set()
    for alert in alerts:
        case = cases.get(alert.case_id)
        if case is None:
            continue
        for user in recipients_for(db, case, alert.rule):
            key = (user.id, alert.case_id, alert.rule)
            if key in already_open:
                continue
            already_open.add(key)
            db.add(
                Notification(
                    user_id=user.id,
                    case_id=alert.case_id,
                    rule=alert.rule,
                    severity=alert.severity,
                    title=RULE_TITLES.get(alert.rule, alert.rule.replace("_", " ").capitalize()),
                    body=f"{case.case_number}: {alert.message}"[:400],
                    is_read=False,
                    details=alert.details or {},
                )
            )
            created += 1
            touched_users.add(user.id)

    return {
        "notifications_created": created,
        "recipients": len(touched_users),
        "alerts_considered": len(alerts),
    }


def notify_user(
    db: Session,
    user_id: int,
    title: str,
    body: str,
    severity: AlertSeverity = AlertSeverity.MEDIUM,
    case_id: int | None = None,
    rule: str | None = None,
    details: dict | None = None,
) -> Notification:
    """Raise a one-off notification — a proposal decision, a hand-off.

    Distinct from fan_out, which is driven by the rule engine. These are
    workflow events with a known single recipient, so there is nothing to
    de-duplicate and no unread constraint to work around (rule is None for
    these, and the partial index treats a NULL rule as distinct).
    """
    notification = Notification(
        user_id=user_id,
        case_id=case_id,
        rule=rule,
        severity=severity,
        title=title[:160],
        body=body[:400],
        is_read=False,
        details=details or {},
    )
    db.add(notification)
    return notification


def notify_case_landowners(
    db: Session,
    case: Case,
    title: str,
    body: str,
    severity: AlertSeverity = AlertSeverity.LOW,
    rule: str | None = None,
) -> int:
    """Notify every landowner account tied to this case, by parcel ownership.

    Ownership, not the affected-family list: a landowner signs in against
    a Person row via User.person_id, and scope_cases_to_user already
    resolves "which cases are this landowner's" the same way (through
    Parcel.owner_id) — this reuses that definition rather than inventing a
    second one that could quietly disagree with it.

    A case can have several owners; each with an account gets their own
    notification, because each is a different person entitled to know.
    Does not commit — the caller owns the transaction, same as fan_out.
    """
    owner_person_ids = db.query(Parcel.owner_id).filter(Parcel.case_id == case.id).distinct()
    landowner_users = (
        db.query(User)
        .filter(
            User.is_active.is_(True),
            User.role == Role.LANDOWNER,
            User.person_id.in_(owner_person_ids),
        )
        .all()
    )
    for owner in landowner_users:
        notify_user(db, owner.id, title, body, severity=severity, case_id=case.id, rule=rule)
    return len(landowner_users)


def notify_objection_filer(
    db: Session,
    objection,
    case: Case,
    title: str,
    body: str,
    severity: AlertSeverity = AlertSeverity.LOW,
) -> bool:
    """Notify the landowner who filed an objection that it has been answered.

    Only the filer, not every landowner on the case — a response to one
    person's objection is that person's news, not a case-wide broadcast.
    An officer can file "on behalf of" someone who has no account of their
    own; that is a no-op here, correctly, since there is nowhere to put it.
    """
    filer = (
        db.query(User)
        .filter(
            User.is_active.is_(True),
            User.role == Role.LANDOWNER,
            User.person_id == objection.person_id,
        )
        .first()
    )
    if filer is None:
        return False
    notify_user(db, filer.id, title, body, severity=severity, case_id=case.id, rule=None)
    return True
