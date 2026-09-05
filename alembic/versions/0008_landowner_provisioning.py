"""add must_change_password to users, and notification_subscriptions

Supports the land-notice search -> subscribe -> credential-provisioning
flow: a landowner never chooses their first password (BhoomiMitra generates
it), so must_change_password marks that it has to be replaced before they
do anything else; notification_subscriptions records who asked to be told
about which parcel, independently of whether they ever get an account.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        'notification_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('parcel_id', sa.Integer(), sa.ForeignKey('parcels.id'), nullable=False),
        sa.Column('whatsapp_number', sa.String(length=15), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('consent_given_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        'ix_notification_subscriptions_parcel_id',
        'notification_subscriptions',
        ['parcel_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_notification_subscriptions_parcel_id', table_name='notification_subscriptions')
    op.drop_table('notification_subscriptions')
    op.drop_column('users', 'must_change_password')
