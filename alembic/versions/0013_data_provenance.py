"""add data provenance tracking

Five columns (data_source, source_name, source_url, retrieved_at,
provenance_status) on every entity a demo might be judged on: districts,
states, villages, projects, cases, parcels, people. See DataSource's
docstring in app.core.enums for what each tier means.

All seven tables already exist, so this is a pure ALTER — none of the
create_all-vs-alembic drift earlier new-table migrations (0009/0010/0012) hit
applies here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ('states', 'districts', 'villages', 'projects', 'cases', 'parcels', 'people')

# See app.core.enums for what these mean. Every seeded row of a place table
# (state/district/village) is real, cited nowhere, so 'public_reference' /
# 'unverified'; every operational row this prototype invents defaults to
# 'synthetic' / 'synthetic'.
PLACE_TABLES = ('states', 'districts', 'villages')
PLACE_SOURCE_NAME = (
    "Real Indian administrative name used for realism; not sourced from a "
    "specific verified dataset in this prototype"
)


def _data_source_enum(create_type: bool) -> postgresql.ENUM:
    return postgresql.ENUM(
        'official', 'public_reference', 'synthetic', name='data_source', create_type=create_type
    )


def _provenance_status_enum(create_type: bool) -> postgresql.ENUM:
    return postgresql.ENUM(
        'verified', 'unverified', 'synthetic', name='provenance_status', create_type=create_type
    )


def upgrade() -> None:
    bind = op.get_bind()
    _data_source_enum(create_type=False).create(bind, checkfirst=True)
    _provenance_status_enum(create_type=False).create(bind, checkfirst=True)

    for table in TABLES:
        op.add_column(
            table,
            sa.Column('data_source', _data_source_enum(create_type=False), nullable=False, server_default='synthetic'),
        )
        op.add_column(table, sa.Column('source_name', sa.String(length=200), nullable=True))
        op.add_column(table, sa.Column('source_url', sa.String(length=500), nullable=True))
        op.add_column(table, sa.Column('retrieved_at', sa.Date(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                'provenance_status',
                _provenance_status_enum(create_type=False),
                nullable=False,
                server_default='synthetic',
            ),
        )

    for table in PLACE_TABLES:
        op.execute(
            f"UPDATE {table} SET data_source = 'public_reference', "
            f"provenance_status = 'unverified', source_name = '{PLACE_SOURCE_NAME}'"
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, 'provenance_status')
        op.drop_column(table, 'retrieved_at')
        op.drop_column(table, 'source_url')
        op.drop_column(table, 'source_name')
        op.drop_column(table, 'data_source')

    _provenance_status_enum(create_type=False).drop(op.get_bind(), checkfirst=True)
    _data_source_enum(create_type=False).drop(op.get_bind(), checkfirst=True)
