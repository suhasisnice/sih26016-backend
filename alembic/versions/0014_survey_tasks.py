"""add survey_tasks and survey_photos

A field survey's lifecycle (assignment through review) and its photo
evidence — see SurveyTask's docstring in app.models.tables for why this is
its own entity rather than another parcel field.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'survey_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('case_id', sa.Integer(), sa.ForeignKey('cases.id'), nullable=False, index=True),
        sa.Column('parcel_id', sa.Integer(), sa.ForeignKey('parcels.id'), nullable=True, index=True),
        sa.Column('assigned_to_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('assigned_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column(
            'status',
            sa.Enum('assigned', 'in_progress', 'submitted', 'approved', 'returned', name='survey_task_status'),
            nullable=False,
            server_default='assigned',
        ),
        sa.Column('due_on', sa.Date(), nullable=True),
        sa.Column('notes', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('measured_area_ha', sa.Float(), nullable=True),
        sa.Column(
            'boundary_geom',
            geoalchemy2.Geometry(geometry_type='POLYGON', srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column(
            'location_geom',
            geoalchemy2.Geometry(geometry_type='POINT', srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_note', sa.String(length=500), nullable=True),
    )
    op.create_index('ix_survey_tasks_boundary_geom', 'survey_tasks', ['boundary_geom'], postgresql_using='gist')
    op.create_index('ix_survey_tasks_location_geom', 'survey_tasks', ['location_geom'], postgresql_using='gist')

    op.create_table(
        'survey_photos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('survey_task_id', sa.Integer(), sa.ForeignKey('survey_tasks.id'), nullable=False, index=True),
        sa.Column('stored_name', sa.String(length=255), nullable=False, unique=True),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('caption', sa.String(length=200), nullable=True),
        sa.Column('uploaded_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('survey_photos')
    op.drop_index('ix_survey_tasks_location_geom', table_name='survey_tasks')
    op.drop_index('ix_survey_tasks_boundary_geom', table_name='survey_tasks')
    op.drop_table('survey_tasks')
    sa.Enum(name='survey_task_status').drop(op.get_bind(), checkfirst=True)
