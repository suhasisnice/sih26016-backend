"""fixed 48-hour invite expiry, and a viewable plaintext copy

Two policy changes to invite codes, both from the same request: every code
now expires exactly `invites.EXPIRY_HOURS` (48) after it is issued,
regardless of role or scope — there is no longer an admin-chosen expiry —
and an administrator can view/copy the full code again until then via a
plaintext copy kept alongside the existing bcrypt hash.

`expires_on` (a date) becomes `expires_at` (a timestamp): the old column
allowed only whole-day precision, which cannot express "48 hours from now".
Existing rows are backfilled to `created_at + 48 hours` — the fixed policy
applies to every invitation the same way, past ones included, since an
already-issued code past that new fixed window is exactly the "dead code
still allowing signup" case there is no reason to grandfather in.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('invite_codes', sa.Column('secret_plain', sa.String(length=64), nullable=True))

    op.add_column('invite_codes', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE invite_codes SET expires_at = created_at + INTERVAL '48 hours'")
    op.alter_column('invite_codes', 'expires_at', nullable=False)
    op.drop_column('invite_codes', 'expires_on')


def downgrade() -> None:
    op.add_column('invite_codes', sa.Column('expires_on', sa.Date(), nullable=True))
    op.execute("UPDATE invite_codes SET expires_on = expires_at::date")
    op.drop_column('invite_codes', 'expires_at')
    op.drop_column('invite_codes', 'secret_plain')
