"""add compensation award components: market value, solatium rate, interest

RFCTLARR Sec. 26-30: an award is market value plus a statutory solatium
(fixed at 100% of market value by Sec. 30(1)) plus Sec. 34 delay interest —
not a single hand-typed total. amount_awarded is now computed by the API
from these three inputs rather than accepted from a client; it stays a real
stored column because a sibling service (sih26016-ai-layer/db/models.py) has
its own model over this same table and reads it directly.

Existing rows predate the split and carry only a final award figure with no
record of how it was reached, so they are backfilled as market value with
zero solatium and zero interest — an honest description of what is actually
known about them, not a guess at their real composition. The total each
household already sees does not move; an officer revising one of these
records fills in real components going forward.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'compensation',
        sa.Column('market_value_amount', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'compensation',
        sa.Column('solatium_rate_pct', sa.Integer(), nullable=False, server_default='100'),
    )
    op.add_column(
        'compensation',
        sa.Column('interest_amount', sa.Integer(), nullable=False, server_default='0'),
    )

    op.execute(
        """
        UPDATE compensation
        SET market_value_amount = amount_awarded,
            solatium_rate_pct = 0,
            interest_amount = 0
        """
    )


def downgrade() -> None:
    op.drop_column('compensation', 'interest_amount')
    op.drop_column('compensation', 'solatium_rate_pct')
    op.drop_column('compensation', 'market_value_amount')
