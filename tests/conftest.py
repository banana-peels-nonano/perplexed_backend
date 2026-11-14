"""Shared test fixtures. pytest loads this file automatically for every test in tests/."""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import get_settings
from app.db import make_engine, make_sessionmaker


def _test_database_url() -> str:
    url = get_settings().test_database_url
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set in .env")
    # Safety net: tests create and drop data. Refuse anything that isn't a *_test database,
    # so a typo in .env can never point the test suite at the dev (or event) database.
    name = urlparse(url).path.lstrip("/")
    if not name.endswith("_test"):
        pytest.fail(f"refusing to run tests against database {name!r}: name must end in _test")
    return url


@pytest.fixture
def test_db_url() -> str:
    """The checked test database URL, for tests that build their own connections."""
    return _test_database_url()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    # One engine per test: each test gets its own event loop (our pytest-asyncio setting),
    # and asyncpg connections cannot be shared across loops.
    eng = make_engine(_test_database_url(), pool_size=5, max_overflow=5)
    yield eng
    await eng.dispose()  # close pooled connections so nothing leaks between tests


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with make_sessionmaker(engine)() as s:
        yield s
