"""widen users.totp_secret/totp_pending_secret to text

Both columns now hold Fernet ciphertext (app.services.crypto.EncryptedString)
rather than a raw base32 secret. A ~32-character plaintext encrypts to
roughly 140 characters once the IV, HMAC and padding are base64-encoded,
well past the VARCHAR(64) sized for the plaintext alone.

biometric_credentials.template needs no change here — it was already
TEXT, so it absorbs the same growth with nothing to migrate.

No data conversion: existing values keep working as plaintext until next
written (a fresh /mfa/totp/setup) — see EncryptedString's decrypt()
fallback for why a mixed old/new column is safe to read.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0020'
down_revision: str | None = '0019'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('users', 'totp_secret', type_=sa.Text(), existing_type=sa.String(64))
    op.alter_column('users', 'totp_pending_secret', type_=sa.Text(), existing_type=sa.String(64))


def downgrade() -> None:
    # Lossy if any current value exceeds 64 characters (any row written
    # after this migration ran, since ciphertext always does) — accepted
    # here the same way other downgrades in this project accept losing
    # what upgrade() gained, never silently.
    op.alter_column('users', 'totp_secret', type_=sa.String(64), existing_type=sa.Text())
    op.alter_column('users', 'totp_pending_secret', type_=sa.String(64), existing_type=sa.Text())
