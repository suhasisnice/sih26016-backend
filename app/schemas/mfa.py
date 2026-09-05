"""Authenticator-app enrollment — managing your own account's TOTP setup.

Distinct from app.schemas.auth's MfaRequiredResponse/MfaVerifyRequest,
which belong to redeeming the second factor at login. These are the
Security-page side: turning it on, confirming it, turning it off.
"""

from pydantic import BaseModel, Field


class MfaStatus(BaseModel):
    totp_enabled: bool


class TotpSetupResponse(BaseModel):
    """The one time the plaintext secret is shown. Held in
    users.totp_pending_secret server-side until POST /mfa/totp/confirm
    proves the person actually copied it into an app, or another /setup
    call replaces it — never trusted from the client on confirm."""

    secret: str
    otpauth_uri: str
    qr_code: str


class TotpConfirmRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
