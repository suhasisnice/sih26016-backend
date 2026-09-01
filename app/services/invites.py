"""Issuing and redeeming registration invitations.

Everything secret about an invitation lives here. Two rules the rest of the
codebase depends on:

1. **A full code exists in memory exactly once**, at the moment it is issued,
   and is returned to the issuer in that one response. It is never stored in
   the clear, never logged, and there is no endpoint that can read it back.
   An administrator who loses a code revokes it and issues another.

2. **Redeeming is constant-time.** A wrong secret and a wrong selector take
   the same work, so the endpoint cannot be used to discover which half of a
   guess was right.
"""

import secrets
from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import Role
from app.core.security import hash_password, verify_password
from app.models import InviteCode

# The human-facing prefix, so a code is recognisable when someone pastes it
# into an email or reads it down a phone line.
PREFIX = "BHM"

SELECTOR_BYTES = 6  # 12 hex characters — public, only needs to be unique
SECRET_BYTES = 24  # 48 hex characters — private, needs to be unguessable

# A bcrypt hash of a value nobody holds. Verified against when the selector
# does not exist, so a bad selector costs the same time as a bad secret and
# the endpoint gives nothing away by responding faster.
_DUMMY_HASH = hash_password(secrets.token_hex(SECRET_BYTES))


def format_code(selector: str, secret: str) -> str:
    return f"{PREFIX}-{selector}-{secret}"


def parse_code(code: str) -> tuple[str, str] | None:
    """Split a presented code into its public and private halves.

    Returns None on anything malformed rather than raising, so the caller
    treats a garbled code exactly like a wrong one.
    """
    if not code:
        return None
    parts = code.strip().upper().split("-")
    if len(parts) != 3:
        return None
    prefix, selector, secret = parts
    if prefix != PREFIX or not selector or not secret:
        return None
    return selector, secret.lower()


def issue(
    db: Session,
    *,
    role: Role,
    district_id: int | None,
    label: str | None,
    state_id: int | None = None,
    organisation: str | None = None,
    max_uses: int,
    expires_on: date | None,
    created_by_user_id: int | None,
) -> tuple[InviteCode, str]:
    """Mint an invitation. Returns the row and the one-time plaintext code."""
    selector = secrets.token_hex(SELECTOR_BYTES).upper()
    secret = secrets.token_hex(SECRET_BYTES)

    invite = InviteCode(
        selector=selector,
        secret_hash=hash_password(secret),
        role=role,
        district_id=district_id,
        state_id=state_id,
        organisation=organisation,
        label=label,
        max_uses=max_uses,
        used_count=0,
        expires_on=expires_on,
        created_by_user_id=created_by_user_id,
    )
    db.add(invite)
    db.flush()

    # Returned separately and deliberately not stored anywhere.
    return invite, format_code(selector, secret)


def redeem_reason(invite: InviteCode | None) -> str | None:
    """Why this invitation cannot be used, or None if it can."""
    if invite is None:
        return "That invitation code is not valid."
    if invite.is_revoked:
        return "That invitation has been withdrawn. Ask the issuing office for another."
    if invite.expires_on is not None and invite.expires_on < date.today():
        return "That invitation has expired. Ask the issuing office for another."
    if invite.used_count >= invite.max_uses:
        return "That invitation has already been used."
    return None


def verify(db: Session, code: str) -> tuple[InviteCode | None, str | None]:
    """Check a presented code.

    Returns (invite, None) when it is good, or (None, reason) when it is not.
    The reason is safe to show: it never reveals whether the selector existed,
    only that the code as a whole cannot be used.
    """
    parsed = parse_code(code)
    if parsed is None:
        # Still pay the bcrypt cost, so a malformed code is not distinguishable
        # by response time from a well-formed wrong one.
        verify_password("", _DUMMY_HASH)
        return None, "That invitation code is not valid."

    selector, secret = parsed
    invite = db.query(InviteCode).filter(InviteCode.selector == selector).first()

    if invite is None:
        verify_password(secret, _DUMMY_HASH)
        return None, "That invitation code is not valid."

    if not verify_password(secret, invite.secret_hash):
        return None, "That invitation code is not valid."

    reason = redeem_reason(invite)
    if reason:
        return None, reason

    return invite, None
