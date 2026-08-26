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


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
