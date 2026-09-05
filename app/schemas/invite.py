"""Registration and invitation schemas.

`InviteCodeOut.code` is the one departure from "a stored code cannot be
read back" — see InviteCode's own docstring in app.models.tables for why. It
is populated only while the invitation is still usable; once
`invites.wipe_dead_secret` clears the database column, this field just
carries None like any other.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Role
from app.schemas.auth import UserOut


class RegisterRequest(BaseModel):
    """Sign up against an invitation.

    The role is NOT accepted from the client. It comes from the invitation,
    so a code issued for a field officer cannot be redeemed into an admin
    account by editing the request.
    """

    invite_code: str = Field(min_length=8, max_length=120)
    username: str = Field(min_length=3, max_length=60, pattern=r"^[a-zA-Z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=120)
    # Twelve rather than eight: these accounts approve compensation and move
    # cases through a statutory process, and the accounts are reachable from
    # whatever network the office is on.
    password: str = Field(min_length=12, max_length=128)


class RegisterResponse(BaseModel):
    """Signed in immediately on success, so registering is one step."""

    access_token: str
    token_type: str = "bearer"
    user: UserOut


class InviteCodePreview(BaseModel):
    """What a code entitles the holder to, shown before they fill the form.

    Safe to return unauthenticated: the caller has already demonstrated they
    hold the code by presenting it, and this tells them nothing they were not
    given along with it.
    """

    valid: bool
    role: Role | None = None
    district_name: str | None = None
    state_name: str | None = None
    organisation: str | None = None
    expires_at: datetime | None = None
    reason: str | None = None


class InviteCodeCreate(BaseModel):
    role: Role
    district_id: int | None = None
    # For a state officer. The route rejects a state-scoped role without
    # one, because such an account would log in and see nothing.
    state_id: int | None = None
    # For a requiring body — the organisation the account files proposals
    # for. Fixed by the issuer, never typed by the person signing up.
    organisation: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=120)
    max_uses: int = Field(default=1, ge=1, le=50)
    # No expires_on field — invites.EXPIRY_HOURS applies to every
    # invitation the same way, not something the issuer chooses.


class InviteCodeOut(BaseModel):
    """An invitation as an administrator sees it afterwards.

    Mostly metadata — except `code`, which carries the full plaintext code
    for as long as it is still usable (see the module docstring) and None
    once it is revoked or past `expires_at`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    selector: str
    code: str | None = None
    role: Role
    district_id: int | None
    district_name: str | None = None
    state_id: int | None = None
    state_name: str | None = None
    organisation: str | None = None
    label: str | None
    max_uses: int
    used_count: int
    expires_at: datetime
    is_revoked: bool
    created_at: datetime


class InviteCodeIssued(BaseModel):
    """The only response that ever contains a full code.

    `code` is generated, returned here, and then exists nowhere but in the
    recipient's hands. It cannot be recovered from the database.
    """

    code: str
    invite: InviteCodeOut


class InviteCodeList(BaseModel):
    items: list[InviteCodeOut]
    total: int


class InviteCheck(BaseModel):
    """Just the code, for the pre-flight check on the signup screen."""

    invite_code: str = Field(min_length=1, max_length=120)
