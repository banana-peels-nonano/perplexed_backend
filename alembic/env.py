"""Alembic environment: how migrations connect to the database.

Async variant (we use asyncpg). Alembic itself is synchronous, so we open an async
connection and hand Alembic a sync view of it with run_sync.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False keeps pytest's and the app's loggers working
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# What autogenerate compares the database against: every table imported in app/models
target_metadata = Base.metadata


def _url() -> str:
    # An explicit URL (tests set one) wins; otherwise DATABASE_URL from .env.
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: open one connection for the migration and close it; no pool to clean up
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
