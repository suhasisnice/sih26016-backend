"""add rnr_benefits

Itemised R&R benefit tracking underneath RnRRecord.status — see the
RnrBenefit model's own comment for why the single overall status isn't
enough on its own.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'rnr_benefits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('rnr_record_id', sa.Integer(), sa.ForeignKey('rnr_records.id'), nullable=False, index=True),
        sa.Column(
            'category',
            sa.Enum('housing', 'land', 'employment', 'annuity', 'other', name='benefit_category'),
            nullable=False,
        ),
        sa.Column('description', sa.String(length=200), nullable=True),
        sa.Column('responsible_department', sa.String(length=120), nullable=True),
        sa.Column('approved_on', sa.Date(), nullable=True),
        sa.Column('expected_on', sa.Date(), nullable=True),
        sa.Column(
            'delivery_status',
            sa.Enum(
                'pending', 'approved', 'in_process', 'delivered', 'failed', 'review_required',
                name='benefit_delivery_status',
            ),
            nullable=False,
            server_default='pending',
        ),
        sa.Column('evidence_document_id', sa.Integer(), sa.ForeignKey('documents.id'), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('updated_on', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('rnr_benefits')
    sa.Enum(name='benefit_delivery_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='benefit_category').drop(op.get_bind(), checkfirst=True)
