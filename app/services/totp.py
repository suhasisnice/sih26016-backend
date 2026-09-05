"""Authenticator-app (TOTP, RFC 6238) enrollment and verification.

A second factor for password login, layered on top of it the same way
face and fingerprint sit above password in the sign-in precedence order:
password proves you know the credential, this proves you also hold the
device it was set up on.

**FALLBACK_CODE is a deliberate, temporary product decision, not a bug.**
Every seeded account (admin, dc.bengaluru, ...) predates this feature and
has no authenticator enrolled, and there is no email/SMS delivery wired up
yet to send them a real one-time code. Rather than lock every existing
account out of password login, or silently skip the second factor for
them, an account with no totp_secret is asked for this fixed code
instead — visibly, in the UI, not hidden as if it were real security. The
moment an account enrolls a real authenticator via /mfa/totp, it stops
accepting this and starts checking real rotating codes.
"""

import base64
import io

import pyotp
import qrcode

ISSUER = "Bhoomimitra"

# See the module docstring. Never checked once a real totp_secret exists.
FALLBACK_CODE = "123456"


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_data_uri(uri: str) -> str:
    """A PNG of `uri`, inline as a data: URI so the frontend needs no
    QR-rendering library of its own — just an <img src>."""
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify(secret: str, code: str) -> bool:
    # valid_window=1 accepts the code from one 30-second step either side
    # of now, which is the difference between "my phone's clock is a few
    # seconds off" and "type faster next time".
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def verify_login_code(totp_secret: str | None, code: str) -> bool:
    """What POST /auth/login/verify actually checks: a real rotating code
    against an enrolled secret, or the fixed fallback if there is none."""
    if totp_secret:
        return verify(totp_secret, code)
    return code.strip() == FALLBACK_CODE
