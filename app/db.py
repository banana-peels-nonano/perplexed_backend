"""Database engine and sessions (SQLAlchemy 2, async, asyncpg driver).

Vocabulary:
  engine          one per process; owns the connection pool.
  sessionmaker    a factory that makes sessions bound to that engine.
  session         one unit of work: borrow a connection, run queries, commit or roll back,
                  give the connection back. One request = one (or a few short) sessions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def make_engine(url: str, *, pool_size: int = 10, max_overflow: int = 10) -> AsyncEngine:
    """Create an engine. Kept separate so tests can point one at the test database."""
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,  # test a pooled connection before use; survives a DB restart
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False (D-005): after commit, objects keep their loaded values.
    # With the default (True), reading an attribute after commit triggers a hidden
    # database query, which in async code raises an error instead of quietly working.
    return async_sessionmaker(engine, expire_on_commit=False)


@lru_cache
def get_engine() -> AsyncEngine:
    s = get_settings()
    return make_engine(s.database_url, pool_size=s.db_pool_size, max_overflow=s.db_max_overflow)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(get_engine())


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed afterwards.

    Endpoints will declare `session: AsyncSession = Depends(get_session)`.
    Tests replace this dependency to use the test database.
    """
    async with get_sessionmaker()() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    """FastAPI dependency for code that needs SEVERAL short transactions in one request
    (the interview: reserve -> call the model with no connection held -> save).
    Tests replace it to point at the test database."""
    return get_sessionmaker()
