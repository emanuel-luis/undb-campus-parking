"""Alembic environment (async). The URL comes from Settings, i.e. PARKING_DATABASE_URL."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from campus_parking.core.db import Base
from campus_parking.core.settings import Settings
from campus_parking.ingestion import models as ingestion_models  # noqa: F401  (registers tables)
from campus_parking.jobs import models as jobs_models  # noqa: F401  (registers tables)
from campus_parking.occupancy import models as occupancy_models  # noqa: F401  (registers tables)
from campus_parking.registry import models as registry_models  # noqa: F401  (registers tables)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Later PRs import their models here so they register on Base.metadata.
target_metadata = Base.metadata


def _url() -> str:
    return Settings().database_url


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Keep autogenerate to schema public (dbt owns warehouse and warehouse_staging).

    Alembic consults this filter for schemas only with include_schemas=True; without it, as
    here, it compares the default schema alone. The filter keeps that true if someone turns
    include_schemas on."""
    return type_ != "schema" or name is None or name == "public"


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database connection."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool, hide_parameters=True)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
