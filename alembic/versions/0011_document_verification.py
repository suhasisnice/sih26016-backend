"""add document verification status

Whether an officer has reviewed a document, separate from its version
state (current/superseded) — see the Document model's own comment on why
these are two different questions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column(
            'verification_status',
            sa.Enum(
                'pending', 'verified', 'rejected', 'correction_requested',
                name='document_verification_status',
            ),
            nullable=False,
            server_default='pending',
        ),
    )
    op.add_column('documents', sa.Column('verification_note', sa.String(length=500), nullable=True))
    op.add_column('documents', sa.Column('verified_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('documents', sa.Column('verified_on', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'verified_on')
    op.drop_column('documents', 'verified_by_user_id')
    op.drop_column('documents', 'verification_note')
    op.drop_column('documents', 'verification_status')
    sa.Enum(name='document_verification_status').drop(op.get_bind(), checkfirst=True)
