"""add grievances, grievance_status_history

A landowner complaint concept distinct from objections — see Grievance's
own docstring in app.models.tables for why the two are not merged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0023'
down_revision: str | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRIEVANCE_CATEGORY_VALUES = (
    'land_property', 'compensation', 'document', 'survey_measurement',
    'notice_notification', 'rehabilitation_resettlement', 'acquisition_objection',
    'delay_in_processing', 'other',
)
GRIEVANCE_STATUS_VALUES = (
    'submitted', 'assigned', 'under_review', 'info_required',
    'response_provided', 'resolved', 'closed',
)
GRIEVANCE_CONTACT_METHOD_VALUES = ('sms', 'email', 'both')


def upgrade() -> None:
    op.create_table(
        'grievances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('grievance_number', sa.String(length=30), nullable=False),
        sa.Column('case_id', sa.Integer(), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('person_id', sa.Integer(), sa.ForeignKey('people.id'), nullable=False),
        sa.Column('filed_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column(
            'category',
            sa.Enum(*GRIEVANCE_CATEGORY_VALUES, name='grievance_category'),
            nullable=False,
        ),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(*GRIEVANCE_STATUS_VALUES, name='grievance_status'),
            nullable=False,
            server_default='submitted',
        ),
        sa.Column(
            'preferred_contact_method',
            sa.Enum(*GRIEVANCE_CONTACT_METHOD_VALUES, name='grievance_contact_method'),
            nullable=False,
            server_default='sms',
        ),
        sa.Column('filed_on', sa.Date(), nullable=False),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('responded_on', sa.Date(), nullable=True),
        sa.Column('responded_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('attachment_stored_name', sa.String(length=80), nullable=True),
        sa.Column('attachment_filename', sa.String(length=255), nullable=True),
        sa.Column('attachment_content_type', sa.String(length=80), nullable=True),
        sa.Column('attachment_size_bytes', sa.Integer(), nullable=True),
        sa.Column('attachment_sha256', sa.String(length=64), nullable=True),
        sa.UniqueConstraint('grievance_number'),
    )
    op.create_index('ix_grievances_grievance_number', 'grievances', ['grievance_number'])
    op.create_index('ix_grievances_case_id', 'grievances', ['case_id'])
    op.create_index('ix_grievances_person_id', 'grievances', ['person_id'])

    op.create_table(
        'grievance_status_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('grievance_id', sa.Integer(), sa.ForeignKey('grievances.id'), nullable=False),
        sa.Column(
            'from_status',
            sa.Enum(*GRIEVANCE_STATUS_VALUES, name='grievance_status', create_type=False),
            nullable=True,
        ),
        sa.Column(
            'to_status',
            sa.Enum(*GRIEVANCE_STATUS_VALUES, name='grievance_status', create_type=False),
            nullable=False,
        ),
        sa.Column('changed_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('changed_on', sa.Date(), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
    )
    op.create_index(
        'ix_grievance_status_history_grievance_id', 'grievance_status_history', ['grievance_id']
    )


def downgrade() -> None:
    op.drop_index('ix_grievance_status_history_grievance_id', table_name='grievance_status_history')
    op.drop_table('grievance_status_history')

    op.drop_index('ix_grievances_person_id', table_name='grievances')
    op.drop_index('ix_grievances_case_id', table_name='grievances')
    op.drop_index('ix_grievances_grievance_number', table_name='grievances')
    op.drop_table('grievances')

    sa.Enum(name='grievance_contact_method').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='grievance_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='grievance_category').drop(op.get_bind(), checkfirst=True)
