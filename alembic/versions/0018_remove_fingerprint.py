"""remove fingerprint: drop kiosk/fingerprint tables, shrink biometric_kind

Fingerprint sign-in (Mantra MFS100 kiosk hardware) is removed from the
product; face and TOTP are untouched. Three tables were fingerprint-only
and are dropped outright. `biometric_credentials` is shared with face and
stays — only its fingerprint rows and the enum value they used are removed.

Drop order respects the foreign keys 0003/0010 created: both
fingerprint_challenges and step_up_challenges reference kiosk_agents, so
they go first.

Shrinking a Postgres enum has no direct ALTER TYPE ... DROP VALUE, so this
recreates the type under a temporary name and casts the one column that
uses it, the standard pattern for this operation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table('fingerprint_challenges')

    op.drop_index('ix_step_up_challenges_nonce', table_name='step_up_challenges')
    op.drop_index('ix_step_up_challenges_user_id', table_name='step_up_challenges')
    op.drop_table('step_up_challenges')

    op.drop_table('kiosk_agents')

    # Purge fingerprint credential rows before the enum they reference shrinks.
    op.execute("DELETE FROM biometric_credentials WHERE kind = 'fingerprint'")

    op.execute("ALTER TYPE biometric_kind RENAME TO biometric_kind_old")
    op.execute("CREATE TYPE biometric_kind AS ENUM ('face')")
    op.execute(
        "ALTER TABLE biometric_credentials "
        "ALTER COLUMN kind TYPE biometric_kind USING kind::text::biometric_kind"
    )
    op.execute("DROP TYPE biometric_kind_old")


def downgrade() -> None:
    op.execute("ALTER TYPE biometric_kind RENAME TO biometric_kind_new")
    op.execute("CREATE TYPE biometric_kind AS ENUM ('face', 'fingerprint')")
    op.execute(
        "ALTER TABLE biometric_credentials "
        "ALTER COLUMN kind TYPE biometric_kind USING kind::text::biometric_kind"
    )
    op.execute("DROP TYPE biometric_kind_new")

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

    op.create_table(
        'step_up_challenges',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('nonce', sa.String(length=64), nullable=False, unique=True),
        sa.Column('kiosk_agent_id', sa.Integer(), sa.ForeignKey('kiosk_agents.id'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_step_up_challenges_user_id', 'step_up_challenges', ['user_id'])
    op.create_index('ix_step_up_challenges_nonce', 'step_up_challenges', ['nonce'], unique=True)
