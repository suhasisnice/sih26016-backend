"""widen public_acquisition_records compensation columns to bigint

A project-level compensation figure can be a crore-scale rupee amount —
the Upper Krishna Project Phase III seed row alone reports 5,440,000,000
(₹544 crore) disbursed, already past a 32-bit integer's ~2.1 billion
ceiling on its own. compensation_awarded/compensation_paid need bigint,
same as every other money column in this schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'public_acquisition_records', 'compensation_awarded',
        type_=sa.BigInteger(), existing_type=sa.Integer(),
    )
    op.alter_column(
        'public_acquisition_records', 'compensation_paid',
        type_=sa.BigInteger(), existing_type=sa.Integer(),
    )


def downgrade() -> None:
    op.alter_column(
        'public_acquisition_records', 'compensation_paid',
        type_=sa.Integer(), existing_type=sa.BigInteger(),
    )
    op.alter_column(
        'public_acquisition_records', 'compensation_awarded',
        type_=sa.Integer(), existing_type=sa.BigInteger(),
    )
