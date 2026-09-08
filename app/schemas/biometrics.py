"""Biometric enrollment and login schemas.

No response model here ever carries a template or an embedding back out —
the same rule invite.py states for invitation codes applies to a face
vector: once stored, this API can compare against it but never return it.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import BiometricKind
from app.schemas.auth import LoginResponse


class FaceEnrollRequest(BaseModel):
    """A single frame, base64-encoded (no data: URL prefix). Sent while
    already authenticated by password — enrollment is something you do to
    your own account, never something a login attempt can trigger."""

    image_base64: str = Field(min_length=1)


class BiometricEnrollResponse(BaseModel):
    kind: BiometricKind
    enrolled_at: datetime


class FaceLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    image_base64: str = Field(min_length=1)


class BiometricStatus(BaseModel):
    """What's enrolled on the current account — drives which options the
    settings screen and the login screen's fallback chain actually show."""

    face_enrolled: bool


class FaceStepUpRequest(BaseModel):
    """Re-confirming the signed-in officer's own face before one
    high-impact action — never a login, so there is no username field:
    whose face this must match is already known from the bearer token."""

    image_base64: str = Field(min_length=1)


class StepUpResponse(BaseModel):
    """Handed to whichever high-impact endpoint the officer is about to
    call, as the X-Stepup-Token header — see
    app.dependencies.verify_stepup. Never reusable for a second action past
    expires_in_seconds, and never a bearer token: typ:"stepup" keeps it out
    of get_current_user entirely."""

    stepup_token: str
    expires_in_seconds: int


__all__ = [
    "BiometricEnrollResponse",
    "BiometricStatus",
    "FaceEnrollRequest",
    "FaceLoginRequest",
    "FaceStepUpRequest",
    "LoginResponse",
    "StepUpResponse",
]
