"""add statutes, statute_stage_references, projects.statute_id

Schema only — no data. Seeded idempotently at boot by
app.services.statutes.seed_defaults, the same pattern
app.services.sla.seed_defaults already uses for stage_sla, rather than
inserted here: this project's convention keeps migrations to shape,
not content.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

revision: str = '0021'
down_revision: str | None = '0020'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'statutes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    op.create_table(
        'statute_stage_references',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('statute_id', sa.Integer(), nullable=False),
        # postgresql.ENUM specifically, not the generic sa.Enum: only the
        # dialect-specific type actually honours create_type=False inside
        # op.create_table. The `stage` type already exists (created for
        # cases.stage) — verified against the offline SQL this migration
        # renders (`alembic upgrade --sql`) rather than assumed, since a
        # regular sa.Enum here silently emits CREATE TYPE stage again and
        # fails with "type already exists" on a real database.
        sa.Column('stage', PGEnum(
            'preliminary_notification', 'social_impact_assessment', 'land_verification',
            'objection_period', 'declaration', 'award', 'rehabilitation_resettlement',
            'possession', 'monitoring', name='stage', create_type=False,
        ), nullable=False),
        sa.Column('is_applicable', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('section_reference', sa.String(length=60), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(['statute_id'], ['statutes.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('statute_id', 'stage', name='uq_statute_stage_references_statute_stage'),
    )
    op.create_index(
        'ix_statute_stage_references_statute_id', 'statute_stage_references', ['statute_id']
    )

    op.add_column('projects', sa.Column('statute_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_projects_statute_id', 'projects', 'statutes', ['statute_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_projects_statute_id', 'projects', type_='foreignkey')
    op.drop_column('projects', 'statute_id')
    op.drop_index('ix_statute_stage_references_statute_id', table_name='statute_stage_references')
    op.drop_table('statute_stage_references')
    op.drop_table('statutes')
