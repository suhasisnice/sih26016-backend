"""add parcels.boundary_field, area_diff_pct, has_boundary_discrepancy

Splits "the boundary" into the boundary of record (`boundary`, unchanged)
and the most recently field-walked one (`boundary_field`, new) so a
re-survey is weighed against the record instead of silently replacing it —
see Parcel's docstring and app.routers.survey.approve_survey_task.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = '0019'
down_revision: str | None = '0018'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'parcels',
        sa.Column(
            'boundary_field',
            geoalchemy2.Geometry(geometry_type='POLYGON', srid=4326, spatial_index=False),
            nullable=True,
        ),
    )
    op.create_index('ix_parcels_boundary_field', 'parcels', ['boundary_field'], postgresql_using='gist')
    op.add_column('parcels', sa.Column('area_diff_pct', sa.Float(), nullable=True))
    op.add_column(
        'parcels',
        sa.Column('has_boundary_discrepancy', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('parcels', 'has_boundary_discrepancy')
    op.drop_column('parcels', 'area_diff_pct')
    op.drop_index('ix_parcels_boundary_field', table_name='parcels')
    op.drop_column('parcels', 'boundary_field')
