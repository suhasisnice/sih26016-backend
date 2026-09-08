"""create public_acquisition_records

Baseline 0001 was generated from create_all()'s output on a database that
already had this table. A database whose actual history predates that
table (create_all() only ever adds, so an older deployment can be missing
tables a newer one has) reaches 0006 with nothing to ALTER: 0006 assumes
the table exists and only widens two of its columns.

This backfills the gap so 0001 -> 0006 works starting from either shape.
On a database that already has the table (matching baseline exactly),
this is a no-op via checkfirst.

Revision ID: 0005a
Revises: 0005
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005a'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'public_acquisition_records' in inspector.get_table_names():
        return

    op.create_table(
        'public_acquisition_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=100), nullable=False),
        sa.Column('record_type', sa.String(length=30), nullable=False),
        sa.Column('project_id', sa.String(length=100), nullable=True),
        sa.Column('project_name', sa.String(length=200), nullable=True),
        sa.Column('case_number_public', sa.String(length=100), nullable=True),
        sa.Column('department', sa.String(length=160), nullable=True),
        sa.Column('implementing_agency', sa.String(length=180), nullable=True),
        sa.Column('district', sa.String(length=100), nullable=True),
        sa.Column('taluk', sa.String(length=100), nullable=True),
        sa.Column('village', sa.String(length=180), nullable=True),
        sa.Column('survey_number', sa.String(length=60), nullable=True),
        sa.Column('land_type', sa.String(length=80), nullable=True),
        sa.Column('nature_of_land', sa.String(length=120), nullable=True),
        sa.Column('notification_type', sa.String(length=100), nullable=True),
        sa.Column('notification_no', sa.String(length=120), nullable=True),
        sa.Column('notification_date', sa.String(length=30), nullable=True),
        sa.Column('status', sa.String(length=80), nullable=True),
        sa.Column('area_ha', sa.Float(), nullable=True),
        sa.Column('area_acres', sa.Float(), nullable=True),
        sa.Column('owner_name_public', sa.Text(), nullable=True),
        sa.Column('owner_data_status', sa.String(length=160), nullable=True),
        # Created BigInteger directly: 0006 (next revision) still runs its
        # ALTER on a fresh database, and that ALTER is a harmless no-op
        # when the column is already BigInteger.
        sa.Column('compensation_awarded', sa.BigInteger(), nullable=True),
        sa.Column('compensation_paid', sa.BigInteger(), nullable=True),
        sa.Column('payment_status', sa.String(length=80), nullable=True),
        sa.Column('source', sa.String(length=200), nullable=True),
        sa.Column('source_reference', sa.Text(), nullable=True),
        sa.Column('is_verified_public', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id'),
    )
    op.create_index(op.f('ix_public_acquisition_records_external_id'), 'public_acquisition_records', ['external_id'], unique=True)
    op.create_index(op.f('ix_public_acquisition_records_record_type'), 'public_acquisition_records', ['record_type'], unique=False)
    op.create_index(op.f('ix_public_acquisition_records_project_id'), 'public_acquisition_records', ['project_id'], unique=False)
    op.create_index(op.f('ix_public_acquisition_records_case_number_public'), 'public_acquisition_records', ['case_number_public'], unique=False)
    op.create_index(op.f('ix_public_acquisition_records_district'), 'public_acquisition_records', ['district'], unique=False)
    op.create_index(op.f('ix_public_acquisition_records_village'), 'public_acquisition_records', ['village'], unique=False)
    op.create_index(op.f('ix_public_acquisition_records_survey_number'), 'public_acquisition_records', ['survey_number'], unique=False)
    op.create_index(op.f('ix_public_acquisition_records_status'), 'public_acquisition_records', ['status'], unique=False)
    op.create_index(op.f('ix_public_acquisition_records_is_verified_public'), 'public_acquisition_records', ['is_verified_public'], unique=False)


def downgrade() -> None:
    op.drop_table('public_acquisition_records')
