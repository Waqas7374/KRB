"""Alembic environment.

Uses the synchronous driver (DATABASE_URL_SYNC) deliberately: migrations are a
single-threaded, transactional operation and the async machinery buys nothing
while complicating error reporting.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base

# Import every module's models so that Base.metadata is complete. Without this,
# autogenerate silently emits DROP TABLE for models it cannot see.
from app.models_registry import import_all_models

import_all_models()

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# PostGIS creates these itself; Alembic must not try to manage them.
EXCLUDED_TABLES = {"spatial_ref_sys", "geography_columns", "geometry_columns"}
EXCLUDED_INDEX_PREFIXES = ("idx_",)


def include_object(  # type: ignore[no-untyped-def]
    obj: object, name: str | None, type_: str, reflected: bool, _compare
) -> bool:
    """Keep PostGIS's own objects out of autogenerate.

    `spatial_ref_sys` and friends are created by the extension, and PostGIS
    names its internal indexes `idx_*`; without this, every autogenerate would
    propose dropping them.
    """
    if type_ == "table" and name in EXCLUDED_TABLES:
        return False
    return not (
        type_ == "index" and name and reflected and name.startswith(EXCLUDED_INDEX_PREFIXES)
    )


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        version_table="alembic_version",
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
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
            version_table="alembic_version",
            # Render batch operations off; PostgreSQL supports real ALTERs.
            render_as_batch=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
