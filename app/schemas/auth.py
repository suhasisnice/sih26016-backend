from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Role


class UserOut(BaseModel):
    """The current user. Deliberately has no password_hash field — a
    response model is the last place a hash should be able to leak from,
    and leaving it out means it cannot, even by accident."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    role: Role
    district_id: int | None = None
    # Resolved from the district relationship rather than left to the client
    # to look up: the top bar names the district an officer is scoped to on
    # every screen, and a second request for one string is not worth it.
    district_name: str | None = None
    # The tier above, for a state officer. Null for everybody else, which is
    # how the frontend decides whether to show a state selector at all.
    state_id: int | None = None
    state_name: str | None = None
    # Set only on a requiring-body account: the organisation it files
    # proposals for, and the scope its proposal list is built from.
    organisation: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    # True only for a landowner account POST /notices/provision created —
    # see User.must_change_password. The frontend routes to a forced
    # "set a new password" step instead of the dashboard when this is true.
    must_change_password: bool = False


class MfaRequiredResponse(BaseModel):
    """What POST /auth/login returns now instead of a token — every
    password login needs the second step at POST /auth/login/verify
    before it issues one. totp_enabled tells the frontend which prompt to
    show: a real rotating code, or app.services.totp's fixed fallback for
    an account that hasn't enrolled an authenticator yet."""

    mfa_required: bool = True
    mfa_token: str
    totp_enabled: bool


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=1, max_length=32)


class SetPasswordRequest(BaseModel):
    """POST /auth/set-password — always the signed-in user's own account,
    never a target user id, so there is nothing here for one account to use
    against another. No current-password field: reaching this endpoint
    already required a valid access token, which is the same authentication
    a current-password re-check would add."""

    new_password: str = Field(min_length=12, max_length=128)
