"""Registering and authenticating fingerprint kiosks.

The same selector/verifier split as app.services.invites, for the same
reason: a kiosk's API key is a bearer secret that must never be readable
back out of the database, only checked against. Where this differs from an
invitation is what the key is *for* — it doesn't grant a role or create an
account, it lets one specific, physically-present scanner ask "does this
fingerprint match this username" and get a session token for someone else
in return. Losing one is losing the ability to forge a login for anyone
who has enrolled a fingerprint at that kiosk, so it is issued once, shown
once, and every use is checked against `is_active`.
"""

import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models import KioskAgent

SELECTOR_BYTES = 6
SECRET_BYTES = 24

# selector.secret — a dot rather than invite_codes' dashes, since this key
# is typed into a config file by whoever installs the kiosk agent, not read
# aloud or copy-pasted from an email, and never needs a human-recognisable
# prefix.
_DUMMY_HASH = hash_password(secrets.token_hex(SECRET_BYTES))


def format_key(selector: str, secret: str) -> str:
    return f"{selector}.{secret}"


def parse_key(key: str) -> tuple[str, str] | None:
    if not key or "." not in key:
        return None
    selector, _, secret = key.partition(".")
    if not selector or not secret:
        return None
    return selector, secret


def issue(
    db: Session,
    *,
    label: str,
    district_id: int | None,
    created_by_user_id: int | None,
) -> tuple[KioskAgent, str]:
    """Register a kiosk. Returns the row and the one-time plaintext key —
    identical shape to invites.issue, and for the same reason nothing else
    in this module can ever hand the key back out again."""
    selector = secrets.token_hex(SELECTOR_BYTES)
    secret = secrets.token_hex(SECRET_BYTES)

    kiosk = KioskAgent(
        selector=selector,
        secret_hash=hash_password(secret),
        label=label,
        district_id=district_id,
        created_by_user_id=created_by_user_id,
        is_active=True,
    )
    db.add(kiosk)
    db.flush()
    return kiosk, format_key(selector, secret)


def authenticate(db: Session, key: str | None) -> KioskAgent | None:
    """The kiosk presenting this key, or None if it does not check out.

    Constant-time in the same sense as invites.verify: a missing selector
    still pays the bcrypt cost against the dummy hash, so "no such kiosk"
    and "wrong secret" take the same time and neither leaks which selectors
    exist.
    """
    parsed = parse_key(key or "")
    if parsed is None:
        verify_password("", _DUMMY_HASH)
        return None

    selector, secret = parsed
    kiosk = db.query(KioskAgent).filter(KioskAgent.selector == selector).first()

    if kiosk is None:
        verify_password(secret, _DUMMY_HASH)
        return None
    if not verify_password(secret, kiosk.secret_hash):
        return None
    if not kiosk.is_active:
        return None

    kiosk.last_used_at = datetime.now(timezone.utc)
    return kiosk
