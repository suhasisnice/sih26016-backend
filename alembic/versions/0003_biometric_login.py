"""biometric login: face + kiosk fingerprint

Three new tables, no changes to anything existing:

- biometric_credentials: one row per enrolled factor per user (face
  embedding or fingerprint template), only one active per (user, kind).
- kiosk_agents: registered fingerprint kiosks, selector/verifier split
  exactly like invite_codes.
- fingerprint_challenges: short-lived nonces tying a kiosk's template
  fetch to the one login attempt it is allowed to grant.

See app/models/tables.py for why each shape is what it is.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'biometric_credentials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('face', 'fingerprint', name='biometric_kind'), nullable=False),
        sa.Column('template', sa.Text(), nullable=False),
        sa.Column('algorithm', sa.String(length=60), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_biometric_credentials_user_id'), 'biometric_credentials', ['user_id'], unique=False
    )
    op.create_index(
        'ix_biometric_credentials_active_per_kind',
        'biometric_credentials',
        ['user_id', 'kind'],
        unique=True,
        postgresql_where=sa.text('is_active = true'),
    )

    op.create_table(
        'kiosk_agents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('selector', sa.String(length=16), nullable=False),
        sa.Column('secret_hash', sa.String(length=255), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('district_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['district_id'], ['districts.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('selector'),
    )
    op.create_index(op.f('ix_kiosk_agents_selector'), 'kiosk_agents', ['selector'], unique=True)

    op.create_table(
        'fingerprint_challenges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kiosk_agent_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('nonce', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['kiosk_agent_id'], ['kiosk_agents.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nonce'),
    )
    op.create_index(
        op.f('ix_fingerprint_challenges_kiosk_agent_id'),
        'fingerprint_challenges', ['kiosk_agent_id'], unique=False,
    )
    op.create_index(
        op.f('ix_fingerprint_challenges_user_id'), 'fingerprint_challenges', ['user_id'], unique=False
    )
    op.create_index(
        op.f('ix_fingerprint_challenges_nonce'), 'fingerprint_challenges', ['nonce'], unique=True
    )


def downgrade() -> None:
    op.drop_table('fingerprint_challenges')
    op.drop_table('kiosk_agents')
    op.drop_table('biometric_credentials')
    op.execute("DROP TYPE IF EXISTS biometric_kind")
