"""Administrative operations. Admin role only, every one of them audited."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.dependencies import (
    DISTRICT_SCOPED_ROLES,
    STATE_SCOPED_ROLES,
    get_db,
    require_role,
)
from app.models import District, InviteCode, State, User
from app.schemas.dashboard import RunRulesResult
from app.schemas.invite import (
    InviteCodeCreate,
    InviteCodeIssued,
    InviteCodeList,
    InviteCodeOut,
)
from app.services import alerts, audit, invites, notify

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
        expires_on=payload.expires_on,
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
    """Every invitation and what has become of it. Metadata only — the codes
    themselves are not recoverable."""
    rows = db.query(InviteCode).order_by(InviteCode.created_at.desc()).all()
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


def _invite_out(db: Session, invite: InviteCode) -> InviteCodeOut:
    return InviteCodeOut(
        id=invite.id,
        selector=invite.selector,
        role=invite.role,
        district_id=invite.district_id,
        district_name=invite.district.name if invite.district else None,
        state_id=invite.state_id,
        state_name=invite.state.name if invite.state else None,
        organisation=invite.organisation,
        label=invite.label,
        max_uses=invite.max_uses,
        used_count=invite.used_count,
        expires_on=invite.expires_on,
        is_revoked=invite.is_revoked,
        created_at=invite.created_at,
    )
