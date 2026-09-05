from pydantic import BaseModel, ConfigDict

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
