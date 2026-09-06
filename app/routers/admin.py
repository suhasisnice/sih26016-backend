"""Administrative operations. Admin role only, every one of them audited."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.core.security import hash_password
from app.dependencies import (
    DISTRICT_SCOPED_ROLES,
    STATE_SCOPED_ROLES,
    get_db,
    require_role,
)
from app.models import District, InviteCode, KioskAgent, State, User
from app.schemas.admin_user import AdminUserList, AdminUserOut, ResetPasswordResponse
from app.schemas.biometrics import KioskAgentCreate, KioskAgentIssued, KioskAgentList, KioskAgentOut
from app.schemas.common import Message
from app.schemas.dashboard import RunRulesResult
from app.schemas.invite import (
    InviteCodeCreate,
    InviteCodeIssued,
    InviteCodeList,
    InviteCodeOut,
)
from app.services import alerts, audit, credentials, invites, kiosk_auth, notify

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/run-rules", response_model=RunRulesResult)
def run_rules(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Re-run every alert rule and rebuild the alerts table.

    Admin only, because it rewrites what every other user sees on their
    dashboard. Safe to run repeatedly: the rules are pure functions of the
    current data, so running it twice in a row produces the same alerts.
    """
    summary = alerts.regenerate_alerts(db)
    # The alerts table is rebuilt from scratch on every run, by design. The
    # inbox is not: fan_out only adds what is not already sitting unread, so
    # running this twice in one night does not hand everybody a second copy
    # of the same finding.
    db.flush()
    delivery = notify.fan_out(db)

    audit.record(
        db,
        user,
        action="admin.run_rules",
        entity_type="alert",
        detail=(
            f"{summary['alerts_generated']} alerts from {summary['cases_evaluated']} cases; "
            f"{delivery['notifications_created']} notifications to "
            f"{delivery['recipients']} recipients"
        ),
    )
    db.commit()
    return RunRulesResult(
        **summary,
        notifications_created=delivery["notifications_created"],
        notification_recipients=delivery["recipients"],
    )


@router.post(
    "/invite-codes",
    response_model=InviteCodeIssued,
    status_code=status.HTTP_201_CREATED,
)
def create_invite_code(
    payload: InviteCodeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Mint a registration invitation.

    **The response is the only time the code exists in readable form.** It is
    stored hashed, so there is no way to recover it afterwards — if it is
    lost, revoke it and issue another. The audit entry records the selector,
    never the code.
    """
    if payload.district_id is not None and db.get(District, payload.district_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="District not found")

    # A district-scoped role without a district would see nothing at all:
    # scope_cases_to_user fails closed for an officer with district_id None.
    if payload.role in DISTRICT_SCOPED_ROLES and payload.district_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{payload.role.value}' works within a district, so the invitation needs one",
        )

    # Same reasoning one tier up. A state officer with no state is scoped to
    # nothing, and an account that can log in but see nothing looks like a
    # bug to whoever redeems the invitation.
    if payload.role in STATE_SCOPED_ROLES:
        if payload.state_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Role '{payload.role.value}' works within a state, so the invitation needs one",
            )
        if db.get(State, payload.state_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="State not found")

    # A requiring body files proposals AS an organisation. Without one the
    # account cannot submit anything and its proposal list is empty.
    if payload.role is Role.REQUIRING_BODY and not payload.organisation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A requiring-body invitation needs the organisation it files for",
        )

    invite, code = invites.issue(
        db,
        role=payload.role,
        district_id=payload.district_id,
        state_id=payload.state_id,
        organisation=payload.organisation,
        label=payload.label,
        max_uses=payload.max_uses,
        created_by_user_id=user.id,
    )

    audit.record(
        db,
        user,
        action="admin.invite_issued",
        entity_type="invite_code",
        entity_id=invite.id,
        detail=f"role={invite.role.value} selector={invite.selector} uses={invite.max_uses}",
    )
    db.commit()
    db.refresh(invite)

    return InviteCodeIssued(code=code, invite=_invite_out(db, invite))


@router.get("/invite-codes", response_model=InviteCodeList)
def list_invite_codes(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Every invitation and what has become of it, including the full code
    while it is still usable — see InviteCodeOut.code.

    There is no background sweep for expiry; a code past `expires_at` still
    has a `secret_plain` sitting in the database until something looks at
    it. This is that something — wiping happens here, on the one screen
    that reads the list, rather than never.
    """
    rows = db.query(InviteCode).order_by(InviteCode.created_at.desc()).all()
    # A list comprehension, not any(...) — any() short-circuits on the first
    # True and would leave every row after the first dead one un-swept.
    wiped = [invites.wipe_dead_secret(row) for row in rows]
    if any(wiped):
        db.commit()
    return InviteCodeList(items=[_invite_out(db, row) for row in rows], total=len(rows))


@router.post("/invite-codes/{invite_id}/revoke", response_model=InviteCodeOut)
def revoke_invite_code(
    invite_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Withdraw an invitation. Accounts already created from it are untouched
    — this stops further use, it does not undo a registration."""
    invite = db.get(InviteCode, invite_id)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

    invite.is_revoked = True
    invite.secret_plain = None
    audit.record(
        db,
        user,
        action="admin.invite_revoked",
        entity_type="invite_code",
        entity_id=invite.id,
        detail=f"selector={invite.selector}",
    )
    db.commit()
    db.refresh(invite)
    return _invite_out(db, invite)


@router.post(
    "/kiosks",
    response_model=KioskAgentIssued,
    status_code=status.HTTP_201_CREATED,
)
def create_kiosk(
    payload: KioskAgentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Register a new fingerprint kiosk.

    **The response is the only time the key exists in readable form** —
    same rule as invite codes, same reason: it is stored hashed and cannot
    be recovered afterwards. Whoever is installing the kiosk agent needs to
    paste this into its config in this one response; if it is lost,
    deactivate the kiosk and register another.
    """
    if payload.district_id is not None and db.get(District, payload.district_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="District not found")

    kiosk, key = kiosk_auth.issue(
        db, label=payload.label, district_id=payload.district_id, created_by_user_id=user.id
    )
    audit.record(
        db,
        user,
        action="admin.kiosk_registered",
        entity_type="kiosk_agent",
        entity_id=kiosk.id,
        detail=f"selector={kiosk.selector} label={kiosk.label}",
    )
    db.commit()
    db.refresh(kiosk)
    return KioskAgentIssued(key=key, kiosk=KioskAgentOut.model_validate(kiosk))


@router.get("/kiosks", response_model=KioskAgentList)
def list_kiosks(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    rows = db.query(KioskAgent).order_by(KioskAgent.created_at.desc()).all()
    return KioskAgentList(
        items=[KioskAgentOut.model_validate(row) for row in rows], total=len(rows)
    )


@router.post("/kiosks/{kiosk_id}/revoke", response_model=Message)
def revoke_kiosk(
    kiosk_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Deactivate a kiosk. Its key stops authenticating immediately —
    nothing already logged in through it is affected, this only stops new
    fingerprint login attempts from that machine."""
    kiosk = db.get(KioskAgent, kiosk_id)
    if kiosk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kiosk not found")

    kiosk.is_active = False
    audit.record(
        db,
        user,
        action="admin.kiosk_revoked",
        entity_type="kiosk_agent",
        entity_id=kiosk.id,
        detail=f"selector={kiosk.selector}",
    )
    db.commit()
    return Message(detail="Kiosk deactivated.")


@router.get("/users", response_model=AdminUserList)
def list_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
    role: Role | None = None,
    district_id: int | None = None,
    is_active: bool | None = None,
    q: str | None = Query(default=None, max_length=120, description="Matches username or full name"),
):
    """Every account on the platform, seeded or invited. Creating one is a
    separate flow (POST /admin/invite-codes, redeemed by the person
    themself) — this is the directory of accounts that already exist, and
    the three things an admin does to one: deactivate, reactivate, reset
    its password.
    """
    query = db.query(User)
    if role is not None:
        query = query.filter(User.role == role)
    if district_id is not None:
        query = query.filter(User.district_id == district_id)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.username.ilike(like), User.full_name.ilike(like)))

    rows = query.order_by(User.role, User.full_name).all()
    return AdminUserList(items=[_user_out(row) for row in rows], total=len(rows))


@router.post("/users/{user_id}/deactivate", response_model=AdminUserOut)
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Block an account from signing in, without touching anything it has
    already done — every case, document and audit row it left behind stays
    exactly as it is. Reversible with the endpoint below."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == user.id:
        # Not a security boundary (an admin could deactivate a second admin
        # account instead) — just refusing the one click that locks the
        # person doing it out of their own session with no one signed in
        # to undo it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account"
        )

    target.is_active = False
    audit.record(
        db, user, action="admin.user_deactivated", entity_type="user", entity_id=target.id,
        detail=f"username={target.username}",
    )
    db.commit()
    db.refresh(target)
    return _user_out(target)


@router.post("/users/{user_id}/reactivate", response_model=AdminUserOut)
def reactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.is_active = True
    audit.record(
        db, user, action="admin.user_reactivated", entity_type="user", entity_id=target.id,
        detail=f"username={target.username}",
    )
    db.commit()
    db.refresh(target)
    return _user_out(target)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def reset_user_password(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Issue a new temporary password for an account that has lost or
    forgotten its own. Same generator app.services.credentials uses to
    provision a landowner's first password, and the same one-time-reveal
    rule as an invite code: this response is the only place it exists in
    readable form, so it must be handed to the account holder now — there
    is no way to come back and look at it again.
    """
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    temporary_password = credentials.generate_temporary_password()
    target.password_hash = hash_password(temporary_password)
    target.must_change_password = True

    audit.record(
        db, user, action="admin.user_password_reset", entity_type="user", entity_id=target.id,
        detail=f"username={target.username}",
    )
    db.commit()

    return ResetPasswordResponse(username=target.username, temporary_password=temporary_password)


def _user_out(row: User) -> AdminUserOut:
    return AdminUserOut(
        id=row.id,
        username=row.username,
        full_name=row.full_name,
        role=row.role,
        district_id=row.district_id,
        district_name=row.district.name if row.district else None,
        state_id=row.state_id,
        state_name=row.state.name if row.state else None,
        organisation=row.organisation,
        is_active=row.is_active,
        must_change_password=row.must_change_password,
        created_at=row.created_at,
    )


def _invite_out(db: Session, invite: InviteCode) -> InviteCodeOut:
    return InviteCodeOut(
        id=invite.id,
        selector=invite.selector,
        code=invites.format_code(invite.selector, invite.secret_plain) if invite.secret_plain else None,
        role=invite.role,
        district_id=invite.district_id,
        district_name=invite.district.name if invite.district else None,
        state_id=invite.state_id,
        state_name=invite.state.name if invite.state else None,
        organisation=invite.organisation,
        label=invite.label,
        max_uses=invite.max_uses,
        used_count=invite.used_count,
        expires_at=invite.expires_at,
        is_revoked=invite.is_revoked,
        created_at=invite.created_at,
    )
