"""field survey data: observations, checklist, photo categories, survey
documents, discrepancy reports

Extends the existing survey_tasks/survey_photos machinery with the
structured data the mobile field-survey wizard needs — land use, boundary
condition, physical features, a checklist snapshot, and on-site-person
fields on survey_tasks; a category on survey_photos; an optional
survey_task_id on documents so a survey report/field note/sketch/
measurement evidence can attach to the fieldwork it came from, not only
the case; and a new survey_discrepancies table, mirroring objections, for
a field officer to flag a mismatch without ever editing the record
directly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # op.add_column, unlike op.create_table, never triggers an enum
    # column's own before_create — that only fires for a CREATE TABLE.
    # Without these explicit create() calls each ALTER TABLE below fails
    # with "type ... does not exist" on any database, fresh included.
    sa.Enum(
        'agricultural', 'residential', 'commercial', 'industrial', 'vacant', 'other',
        name='land_use_type',
    ).create(op.get_bind(), checkfirst=True)
    op.add_column(
        'survey_tasks',
        sa.Column(
            'land_use',
            sa.Enum(
                'agricultural', 'residential', 'commercial', 'industrial', 'vacant', 'other',
                name='land_use_type',
            ),
            nullable=True,
        ),
    )
    sa.Enum(
        'verified', 'partially_verified', 'not_clearly_identifiable',
        name='boundary_condition',
    ).create(op.get_bind(), checkfirst=True)
    op.add_column(
        'survey_tasks',
        sa.Column(
            'boundary_condition',
            sa.Enum(
                'verified', 'partially_verified', 'not_clearly_identifiable',
                name='boundary_condition',
            ),
            nullable=True,
        ),
    )
    op.add_column(
        'survey_tasks',
        sa.Column('physical_features', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'survey_tasks',
        sa.Column('checklist', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column('survey_tasks', sa.Column('on_site_person_name', sa.String(length=120), nullable=True))
    op.add_column('survey_tasks', sa.Column('on_site_person_relation', sa.String(length=80), nullable=True))
    op.add_column(
        'survey_tasks',
        sa.Column('person_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('survey_tasks', sa.Column('person_verification_note', sa.Text(), nullable=True))

    sa.Enum(
        'land_parcel', 'boundary', 'existing_structure', 'crop_land_use', 'road_access',
        'nearby_structure', 'survey_marker', 'other',
        name='survey_photo_category',
    ).create(op.get_bind(), checkfirst=True)
    op.add_column(
        'survey_photos',
        sa.Column(
            'category',
            sa.Enum(
                'land_parcel', 'boundary', 'existing_structure', 'crop_land_use', 'road_access',
                'nearby_structure', 'survey_marker', 'other',
                name='survey_photo_category',
            ),
            nullable=True,
        ),
    )

    op.add_column(
        'documents',
        sa.Column('survey_task_id', sa.Integer(), sa.ForeignKey('survey_tasks.id'), nullable=True),
    )
    # op.add_column does not honour a Column's index=True (there is no
    # CREATE TABLE happening for it to attach to) — the index needs its own
    # explicit statement, unlike the columns declared inside 0016's
    # op.create_table above.
    op.create_index('ix_documents_survey_task_id', 'documents', ['survey_task_id'])

    # New DocType values. doc_type is a plain sa.Enum bound to a database
    # type created back in 0001 with every value it had at the time — this
    # is the first migration to add to it. ALTER TYPE ... ADD VALUE is safe
    # inside a transaction on Postgres 12+ (this deployment runs 16) as
    # long as the new value is not also used within the same transaction,
    # which nothing here does.
    for value in ('survey_report', 'field_note', 'sketch', 'measurement_evidence'):
        op.execute(f"ALTER TYPE doc_type ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        'survey_discrepancies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('survey_task_id', sa.Integer(), sa.ForeignKey('survey_tasks.id'), nullable=False, index=True),
        sa.Column('case_id', sa.Integer(), sa.ForeignKey('cases.id'), nullable=False, index=True),
        sa.Column(
            'discrepancy_type',
            sa.Enum(
                'area_mismatch', 'boundary_mismatch', 'survey_number_mismatch', 'ownership_mismatch',
                'land_use_mismatch', 'missing_document', 'other',
                name='discrepancy_type',
            ),
            nullable=False,
        ),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('open', 'resolved', name='discrepancy_status'),
            nullable=False,
            server_default='open',
        ),
        sa.Column('filed_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('filed_on', sa.Date(), nullable=False),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('responded_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('responded_on', sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('survey_discrepancies')
    sa.Enum(name='discrepancy_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='discrepancy_type').drop(op.get_bind(), checkfirst=True)

    # Postgres cannot drop a single enum VALUE — the four doc_type additions
    # above are left in place on downgrade, harmless and unused, same
    # trade-off every earlier doc_type addition in this history makes.

    op.drop_index('ix_documents_survey_task_id', table_name='documents')
    op.drop_column('documents', 'survey_task_id')

    op.drop_column('survey_photos', 'category')
    sa.Enum(name='survey_photo_category').drop(op.get_bind(), checkfirst=True)

    op.drop_column('survey_tasks', 'person_verification_note')
    op.drop_column('survey_tasks', 'person_verified')
    op.drop_column('survey_tasks', 'on_site_person_relation')
    op.drop_column('survey_tasks', 'on_site_person_name')
    op.drop_column('survey_tasks', 'checklist')
    op.drop_column('survey_tasks', 'physical_features')
    op.drop_column('survey_tasks', 'boundary_condition')
    sa.Enum(name='boundary_condition').drop(op.get_bind(), checkfirst=True)
    op.drop_column('survey_tasks', 'land_use')
    sa.Enum(name='land_use_type').drop(op.get_bind(), checkfirst=True)
