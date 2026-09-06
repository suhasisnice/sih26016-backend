"""Password hashing and JWT issue/verify.

Passwords are bcrypt-hashed, never stored or logged in the clear, and the
API never returns a hash in any response schema.
"""

from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


# Five minutes: long enough to type a 6-digit code, short enough that a
# token intercepted between the password step and the code step is
# useless well before anyone would think to try it.
MFA_TOKEN_EXPIRE_MINUTES = 5


def create_mfa_token(user_id: int) -> str:
    """A token proving "the password step just passed for this user",
    nothing more. `typ: "mfa"` is what keeps it from working as a bearer
    token — get_current_user rejects any token carrying it, so this can
    only ever be redeemed at POST /auth/login/verify, never used to reach
    an authenticated route directly."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "typ": "mfa",
        "iat": now,
        "exp": now + timedelta(minutes=MFA_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


# Ten minutes: long enough to walk from a desk to the kiosk scanner and
# back, short enough that a token minted for one approval cannot be sat on
# and reused for an unrelated action minutes later.
STEPUP_TOKEN_EXPIRE_MINUTES = 10


def create_stepup_token(user_id: int) -> str:
    """Proof that THIS user freshly re-confirmed their own identity by
    face or fingerprint, for one high-impact action — never a login, and
    never redeemable as one. `typ: "stepup"` keeps it out of
    get_current_user the same way `typ: "mfa"` already is; a route that
    requires step-up checks this token itself (see
    app.dependencies.verify_stepup)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "typ": "stepup",
        "iat": now,
        "exp": now + timedelta(minutes=STEPUP_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict | None:
    """Returns the payload, or None if the token is invalid or expired.

    The role is re-read from the database on every request rather than
    trusted from this payload — a token minted before a role changed must
    not keep the old privileges alive until it expires.
    """
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.PyJWTError:
        return None
