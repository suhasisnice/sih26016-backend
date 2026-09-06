"""The State Administrator's user directory — list every account, and the
three things an admin does to one from here: deactivate it, reactivate it,
or reset its password. Creating an account is a separate flow (an invite
code, redeemed by the person themself) — this is what happens to an
account that already exists.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.enums import Role


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    role: Role
    district_id: int | None
    district_name: str | None
    state_id: int | None
    state_name: str | None
    organisation: str | None
    is_active: bool
    must_change_password: bool
    created_at: datetime


class AdminUserList(BaseModel):
    items: list[AdminUserOut]
    total: int


class ResetPasswordResponse(BaseModel):
    """The new password exists in readable form exactly once — this
    response — the same rule invite codes and kiosk keys follow. It is
    never stored anywhere but hashed, so if this is lost the only recovery
    is to reset again."""

    username: str
    temporary_password: str
