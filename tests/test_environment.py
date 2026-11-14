"""Proves the development environment is wired correctly.

If this passes: Python can read .env, reach Postgres on 127.0.0.1, log in,
and the separate test database exists. Delete it once real tests cover the same ground.
"""

import os
from pathlib import Path

import asyncpg


def _test_db_dsn() -> str:
    # Read TEST_DATABASE_URL from the environment, falling back to the .env file.
    url = os.environ.get("TEST_DATABASE_URL")
    if not url and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.startswith("TEST_DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    assert url, "TEST_DATABASE_URL is not set in the environment or .env"
    # SQLAlchemy URLs say 'postgresql+asyncpg://'; asyncpg itself wants 'postgresql://'.
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def test_can_reach_test_database():
    conn = await asyncpg.connect(_test_db_dsn(), timeout=5)
    try:
        name = await conn.fetchval("SELECT current_database()")
        version = await conn.fetchval("SHOW server_version")
    finally:
        await conn.close()
    assert name == "perplexed_test"
    assert version.startswith("17")
