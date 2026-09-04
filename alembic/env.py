"""Alembic environment.

Two things here are not boilerplate and matter:

1. The URL comes from `app.database.DATABASE_URL`, not from alembic.ini.
   That is the already-normalised value — `postgres://` rewritten,
   `pgbouncer=true` stripped — so a migration connects with exactly the
   string the application connects with. An alembic.ini with its own copy
   is how a migration ends up applied to a different database than the one
   the API is reading.

2. GeoAlchemy2's alembic helpers are installed. Without them autogenerate
   emits `sa.NullType()` for every geometry column, and it tries to create
   the `spatial_ref_sys` / `geometry_columns` tables PostGIS already owns.
   The helpers teach it to render `Geometry(...)` and to leave PostGIS's
   own objects alone.
"""

from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  registers every table on Base.metadata
from app.database import DATABASE_URL, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=alembic_helpers.include_object,
        render_item=alembic_helpers.render_item,
        # Without this a column that only changed type — Integer to
        # BigInteger, say — autogenerates as nothing at all.
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=alembic_helpers.include_object,
            render_item=alembic_helpers.render_item,
            process_revision_directives=alembic_helpers.writer,
            compare_type=True,
            compare_server_default=True,
            # DDL in PostgreSQL is transactional. Running the whole upgrade
            # in one transaction means a revision that fails halfway leaves
            # the schema exactly as it was, rather than half-migrated with
            # no revision recorded to tell you which half.
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
