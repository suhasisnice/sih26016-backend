"""drop the duplicate parcel geometry index

0001's docstring already promised this one: a database stamped at 0001
carries `idx_parcels_geom`, a GiST index GeoAlchemy2 created automatically
alongside the explicit one this model also declares. A fresh database
never gets the duplicate in the first place, so this converges the two —
`IF EXISTS` makes it a no-op there rather than a failure.
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_parcels_geom")


def downgrade() -> None:
    # The duplicate was GeoAlchemy2's own doing, not this model's — nothing
    # here should recreate it. A downgrade that wants the stamped
    # database's exact prior state back is restoring from backup, not
    # replaying migrations.
    pass
