"""add step_up_challenges

A fresh biometric re-confirmation before a high-impact case action
(hold, or advancing into Declaration/Award/Possession/Monitoring) — see
app.dependencies.verify_stepup and app.routers.biometrics' stepup
endpoints. Separate from fingerprint_challenges (login) because the
kiosk_agent_id there is meaningfully required at creation time; here it
isn't known until a kiosk actually reports.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index('ix_step_up_challenges_nonce', table_name='step_up_challenges')
    op.drop_index('ix_step_up_challenges_user_id', table_name='step_up_challenges')
    op.drop_table('step_up_challenges')
