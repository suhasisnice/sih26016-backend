"""one compensation award per person per case

POST /compensation now lets an officer declare a new award, alongside the
existing PATCH that revises one. Without a database constraint, a race
between two concurrent declarations for the same household would leave two
award rows where the Act recognises one; the router's own pre-check closes
the common case but not a true race, so the constraint is what actually
guarantees it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        'uq_compensation_case_person', 'compensation', ['case_id', 'person_id']
    )


def downgrade() -> None:
    op.drop_constraint('uq_compensation_case_person', 'compensation', type_='unique')
