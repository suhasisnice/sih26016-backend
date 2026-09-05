"""add notification_logs

One row per WhatsApp/email send attempt against a NotificationSubscription
— see app.services.landowner_notify. Independent of the subscription table
added in 0008: a subscription is a standing request, this is the history of
what was actually (attempted to be) sent against it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Inline sa.Enum columns create their backing Postgres type themselves
    # as part of create_table (the same pattern 0001's Stage/Role columns
    # use) — a separate explicit .create() call first would just race it
    # for the same type name.
    op.create_table(
        'notification_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('parcel_id', sa.Integer(), sa.ForeignKey('parcels.id'), nullable=False),
        sa.Column(
            'channel',
            sa.Enum('in_app', 'email', 'sms', 'whatsapp', name='notification_channel'),
            nullable=False,
        ),
        sa.Column('notification_type', sa.String(length=40), nullable=False),
        sa.Column('recipient', sa.String(length=255), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'sent', 'failed', name='notification_log_status'),
            nullable=False,
        ),
        sa.Column('is_mock', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_notification_logs_parcel_id', 'notification_logs', ['parcel_id'])


def downgrade() -> None:
    op.drop_index('ix_notification_logs_parcel_id', table_name='notification_logs')
    op.drop_table('notification_logs')
    sa.Enum(name='notification_log_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='notification_channel').drop(op.get_bind(), checkfirst=True)
