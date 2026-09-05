"""add totp_secret and totp_pending_secret to users

Second factor for password login: an authenticator app (TOTP, RFC 6238).
Both columns are nullable — an account with neither has no authenticator
enrolled, which app.services.totp's login-time check treats as "use the
fixed fallback code" rather than as an error.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('totp_secret', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('totp_pending_secret', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'totp_pending_secret')
    op.drop_column('users', 'totp_secret')
