"""add push notification channel and subscription storage

Web Push via VAPID — no vendor account, no approval process, unlike the
SMS channel this follows. A citizen's browser subscription (endpoint +
encryption keys, the shape the Push API itself defines) is stored
alongside the whatsapp_number/email columns already on
notification_subscriptions, the same "one row, several optional channels"
shape those two already use.

'push' is added to notification_channel with ALTER TYPE ... ADD VALUE
rather than the drop-and-recreate dance 0018_remove_fingerprint needed —
Postgres has supported adding an enum value inside a transaction since
version 12, the constraint is only that the new value can't be read back
within that same transaction, and nothing here does that.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_channel ADD VALUE IF NOT EXISTS 'push'")

    op.add_column(
        'notification_subscriptions',
        sa.Column(
            'push_subscription',
            sa.Text(),
            nullable=True,
            comment=(
                "JSON-serialised browser PushSubscription (endpoint + p256dh/auth "
                "keys) from the Push API. Opaque to this app — handed to pywebpush "
                "as-is at send time."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column('notification_subscriptions', 'push_subscription')
    # Postgres has no ALTER TYPE ... DROP VALUE — removing 'push' from
    # notification_channel would need the same recreate-the-type migration
    # 0018_remove_fingerprint used for biometric_kind. Not done here: an
    # unused enum value is harmless, and this downgrade already removes
    # everything that could write one.
