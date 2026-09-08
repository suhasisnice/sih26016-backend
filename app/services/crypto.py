"""Symmetric at-rest encryption for a small set of especially sensitive
columns: TOTP secrets and biometric templates. A column opts in by
declaring `EncryptedString` instead of `String`/`Text` — nothing outside
this file and the model declaration needs to change; SQLAlchemy calls
process_bind_param/process_result_value on every write and read.

**Why not everything.** Encrypting every column would hide the far more
common ways this data is actually exposed — a compromised officer
account, an unscoped query — behind a control that only helps against one
narrow threat: someone reading the database file or a backup directly,
bypassing the application entirely. Reserved for the two column kinds
where that threat is worth the cost: a TOTP secret read once is a live
bypass of a second factor, and a biometric template is the one kind of
data in this system a person cannot rotate if it leaks.

**Key management, honestly.** `settings.encryption_key` behaves like
SECRET_KEY: a built-in development value anyone can read in this
repository. Unlike SECRET_KEY, it is deliberately NOT added to
config.validate_for_environment's production boot refusal — a wrong
SECRET_KEY forges nobody's login, but an encryption key that turns out to
be wrong (or gets rotated without a plan) makes the data it protected
permanently unreadable. Refusing to boot without one risks a redeploy
that cannot decrypt yesterday's rows; this is a choice a deployer makes
deliberately (set ENCRYPTION_KEY to `Fernet.generate_key().decode()`),
not one the app can safely force.

**Reading old, unencrypted rows.** A row written before this existed, or
before a deployment's key was set, holds a value Fernet cannot parse.
decrypt() treats that as plaintext rather than raising — an existing TOTP
secret or template keeps working exactly as it did, and starts being
encrypted the moment it is next written (a fresh /mfa/totp/setup, a fresh
/biometrics/face/enroll).
"""

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.encryption_key.encode("ascii"))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Not a Fernet token at all — see "Reading old, unencrypted rows"
        # above. Also covers a value encrypted under a DIFFERENT key than
        # the one currently configured: same symptom, same honest
        # fallback, rather than a 500 on every login.
        return value


class EncryptedString(TypeDecorator):
    """A text column, encrypted at rest.

    Stored as TEXT regardless of the plaintext's own natural length:
    Fernet's overhead (a version byte, a timestamp, a 16-byte IV, PKCS7
    padding to the next block, a 32-byte HMAC, then base64) roughly
    doubles a short value and pushes it well past a VARCHAR(64) sized for
    the plaintext — a 32-character TOTP secret encrypts to around 140
    characters. Unbounded avoids re-litigating a width for every future
    column this is used on.

    Equality/LIKE filtering in SQL will not work against it — Fernet's own
    random nonce means the same plaintext never encrypts to the same bytes
    twice. Fine for every column this is used on today: nothing queries a
    TOTP secret or a biometric template by value, only by presence
    (`is not None`) after loading the owning row some other way.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt(value)
