"""Generating login credentials for a landowner account that BhoomiMitra
provisions itself, from a verified land record — see
app.routers.notices' POST /notices/provision for the flow this supports.

Distinct from app.services.invites: an invite is typed in by hand and
redeemed later by whoever holds it; this generates a working account
immediately, in one step, because the person's identity is the land record
itself rather than a code someone handed them.
"""

import secrets
import string

from sqlalchemy.orm import Session

from app.models import User

USERNAME_PREFIX = "BM"
_USERNAME_DIGITS = 6
_MAX_ATTEMPTS = 20

# Excludes visually ambiguous characters (0/O, 1/l/I) — this password is
# typed once, off a phone or a screen, often read aloud.
_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
_PASSWORD_LENGTH = 10


def generate_username(db: Session) -> str:
    """BM-XXXXXX — one consistent pattern across every landowner account
    this flow provisions. Deliberately carries no district, case or name
    information: a username should not leak who someone is or where their
    land is just by being read over someone's shoulder."""
    for _ in range(_MAX_ATTEMPTS):
        digits = "".join(secrets.choice(string.digits) for _ in range(_USERNAME_DIGITS))
        candidate = f"{USERNAME_PREFIX}-{digits}"
        if db.query(User.id).filter(User.username == candidate).first() is None:
            return candidate
    raise RuntimeError("Could not generate a unique BM- username after 20 attempts")


def generate_temporary_password() -> str:
    """Never persisted anywhere but the caller's one HTTP response — the
    database only ever sees hash_password()'s output, the same rule every
    other password in this system follows."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))
