"""add consent tracking and ULPIN columns missing since the consolidation

0001 was hand-baselined from the schema as deployed at the time, and missed
three columns the models already declared: `affected_families.consent_given`
and `cases.consent_threshold_pct` (Sec. 2(2) consent tracking) and
`parcels.ulpin`. Any database stamped at 0001..0003 is missing them outright,
which surfaces as `UndefinedColumn` the first time a query touches one of
these three tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # affected_families already has 325 rows on a seeded database, so the
    # NOT NULL column needs a server-side default to backfill them; the
    # default is then dropped so the column matches the model exactly
    # (nullable=False, default=False is an ORM-insert-time default only).
    op.add_column(
        'affected_families',
        sa.Column('consent_given', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('affected_families', 'consent_given', server_default=None)

    op.add_column('cases', sa.Column('consent_threshold_pct', sa.Float(), nullable=True))

    op.add_column('parcels', sa.Column('ulpin', sa.String(length=14), nullable=True))
    op.create_index(op.f('ix_parcels_ulpin'), 'parcels', ['ulpin'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_parcels_ulpin'), table_name='parcels')
    op.drop_column('parcels', 'ulpin')
    op.drop_column('cases', 'consent_threshold_pct')
    op.drop_column('affected_families', 'consent_given')
