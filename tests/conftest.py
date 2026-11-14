"""Shared test fixtures. pytest loads this file automatically for every test in tests/."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic import command
from app.config import get_settings
from app.db import get_session, make_engine, make_sessionmaker, session_factory
from app.llm.fake import FakeProvider
from app.main import create_app
from app.models import Base
from app.seed import run_seed

FIXTURE = Path(__file__).parent / "fixtures" / "northfield.toml"


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


def _upgrade_to_head(url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


@pytest.fixture
async def db(engine: AsyncEngine, test_db_url: str) -> AsyncIterator[AsyncEngine]:
    """A migrated, EMPTY test database.

    Alembic's env.py calls asyncio.run(), which can't run inside this test's event loop,
    so the upgrade runs in a worker thread (asyncio.to_thread), where no loop is running.
    Upgrading an already-current schema is a no-op, so this is cheap.
    """
    await asyncio.to_thread(_upgrade_to_head, test_db_url)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine


@pytest.fixture
async def seeded(db: AsyncEngine) -> dict[str, str]:
    """Fixture case loaded, one team per company. Returns {team code: access code}."""
    async with make_sessionmaker(db)() as session, session.begin():
        report = await run_seed(session, FIXTURE, teams_per_company=1, start_phase_key="solo")
    return report.new_codes


@pytest.fixture
def fake() -> FakeProvider:
    """The model every test app uses. Tests script it: fake.queues["answer"].append({...})."""
    return FakeProvider()


@pytest.fixture
async def make_client(
    db: AsyncEngine, fake: FakeProvider
) -> AsyncIterator[Callable[[], httpx.AsyncClient]]:
    """Factory for HTTP clients that talk to the app in-process (no server, no network).

    Each client is like a separate laptop: its own cookie jar. Every request's database
    session comes from the test database instead of DATABASE_URL.
    """
    app = create_app(provider=fake)
    maker = make_sessionmaker(db)

    async def _test_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _test_session
    app.dependency_overrides[session_factory] = lambda: maker
    clients: list[httpx.AsyncClient] = []

    def _make() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()
