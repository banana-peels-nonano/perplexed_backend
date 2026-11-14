"""Step 1: settings load, and our own engine/session code reaches the test database."""

import asyncio

from sqlalchemy import text

from app.config import get_settings


def test_settings_load_and_hide_secrets():
    s = get_settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.test_database_url and s.test_database_url.endswith("_test")
    # SecretStr keeps keys out of logs and tracebacks
    assert "sk-" not in repr(s)


async def test_session_runs_a_query(session):
    result = await session.execute(text("SELECT current_database(), 1 + 1"))
    name, two = result.one()
    assert name == "perplexed_test"
    assert two == 2


async def test_pool_serves_concurrent_sessions(engine):
    # 8 sessions at once against a pool of 5 + 5: all must succeed.
    # pg_sleep holds each connection briefly so they genuinely overlap.
    from app.db import make_sessionmaker

    maker = make_sessionmaker(engine)

    async def one(i: int) -> int:
        async with maker() as s:
            await s.execute(text("SELECT pg_sleep(0.1)"))
            return (await s.execute(text("SELECT CAST(:i AS integer)"), {"i": i})).scalar_one()

    results = await asyncio.gather(*(one(i) for i in range(8)))
    assert sorted(results) == list(range(8))
