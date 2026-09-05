"""Biometric enrollment and login schemas.

No response model here ever carries a template or an embedding back out —
the same rule invite.py states for invitation codes applies to a face
vector or a fingerprint template: once stored, this API can compare
against it but never return it.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
    fingerprint_enrolled: bool


class FingerprintEnrollRequest(BaseModel):
    """The kiosk agent's local capture, forwarded by the browser exactly as
    the agent returned it — this endpoint does not talk to the kiosk
    directly, only the already-authenticated browser session does."""

    template_base64: str = Field(min_length=1)


class FingerprintChallengeRequest(BaseModel):
    """What a kiosk agent sends to start a fingerprint login attempt for
    the username someone typed at the kiosk. Requires the X-Kiosk-Key
    header — see app.services.kiosk_auth."""

    username: str = Field(min_length=1, max_length=60)


class FingerprintChallengeResponse(BaseModel):
    """Handed to the kiosk agent to run its own local capture-and-match
    against. `template_base64` is the enrolled template — see
    BiometricCredential's docstring for why only the agent that captured it
    is trusted to receive it back."""

    challenge_nonce: str
    template_base64: str
    expires_in_seconds: int


class FingerprintLoginRequest(BaseModel):
    """The kiosk agent's verdict after running MFS100MatchISO locally.

    `score` is required and re-checked against the server's own threshold
    (app.services.kiosk_auth's caller does this, not the agent) rather than
    trusting a bare boolean — a compromised or buggy agent that always
    sends matched=true is a much easier bug/attack than one that also has
    to fabricate a plausible score every single field of which the server
    would otherwise take on faith.
    """

    challenge_nonce: str
    score: int = Field(ge=0, le=100_000)


class KioskAgentCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    district_id: int | None = None


class KioskAgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    selector: str
    label: str
    district_id: int | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None


class KioskAgentIssued(BaseModel):
    """The only response that ever contains a usable key — see
    KioskAgentOut's sibling InviteCodeIssued for the same reasoning."""

    key: str
    kiosk: KioskAgentOut


class KioskAgentList(BaseModel):
    items: list[KioskAgentOut]
    total: int


__all__ = [
    "BiometricEnrollResponse",
    "BiometricStatus",
    "FaceEnrollRequest",
    "FaceLoginRequest",
    "FingerprintChallengeRequest",
    "FingerprintChallengeResponse",
    "FingerprintEnrollRequest",
    "FingerprintLoginRequest",
    "KioskAgentCreate",
    "KioskAgentIssued",
    "KioskAgentList",
    "KioskAgentOut",
    "LoginResponse",
]
