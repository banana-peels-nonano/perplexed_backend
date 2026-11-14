"""Step 2: the migration builds the schema from nothing, undoes it cleanly, and matches the models.

These tests are plain (sync) functions on purpose: Alembic's env.py starts its own event
loop with asyncio.run(), which is not allowed inside an already-running async test.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command

EXPECTED_TABLES = {
    "company",
    "department",
    "stakeholder",
    "record",
    "passage",
    "stakeholder_passage_access",
    "event",
    "phase",
    "team",
    "team_session",
    "team_stakeholder_state",
    "message",
    "team_disclosure",
    "team_board",
    "llm_call",
}


def _run(coro):
    return asyncio.run(coro)


async def _sql(url: str, *statements: str):
    """Run statements in one transaction; return the last one's rows.

    asyncpg sends each statement as a prepared statement, and a prepared statement
    may contain only ONE command, so statements are passed separately, not joined by ';'.
    """
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = None
            for statement in statements:
                result = await conn.execute(text(statement))
            return result.fetchall() if result is not None and result.returns_rows else None
    finally:
        await engine.dispose()


def _tables(url: str) -> set[str]:
    rows = _run(
        _sql(url, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    return {r[0] for r in rows} - {"alembic_version"}


@pytest.fixture
def alembic_cfg(test_db_url: str) -> Config:
    url = test_db_url
    cfg = Config("alembic.ini")
    # ConfigParser treats % specially, so escape it in case a password contains one
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # start every migration test from a truly empty schema
    _run(_sql(url, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public"))
    return cfg


def test_upgrade_creates_every_table_and_downgrade_removes_them(alembic_cfg, test_db_url):
    url = test_db_url
    command.upgrade(alembic_cfg, "head")
    assert _tables(url) == EXPECTED_TABLES

    command.downgrade(alembic_cfg, "base")
    assert _tables(url) == set()

    # and back up again, leaving the schema in place for later tests
    command.upgrade(alembic_cfg, "head")
    assert _tables(url) == EXPECTED_TABLES


def test_models_and_migrations_agree(alembic_cfg):
    # `alembic check` autogenerates a diff between the models and the migrated database;
    # it raises if a model changed without a matching migration.
    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)


def test_constraints_enforce_fairness_rules(alembic_cfg, test_db_url):
    """Two rules the database itself guarantees, whatever the application code does."""
    url = test_db_url
    command.upgrade(alembic_cfg, "head")
    _run(
        _sql(
            url,
            "INSERT INTO company (id, name) VALUES ('A', 'Northfield')",
            "INSERT INTO department (company_id, name) VALUES ('A', 'finance')",
            "INSERT INTO stakeholder (id, company_id, department, name, role, persona, canary)"
            " VALUES ('cfo', 'A', 'finance', 'R', 'CFO', 'p', 'c')",
            "INSERT INTO team (id, code, name, company_id, access_code_hash)"
            " VALUES ('00000000-0000-0000-0000-000000000001', 'T1', 'Team 1', 'A', 'h')",
            "INSERT INTO team_stakeholder_state (team_id, stakeholder_id, quota)"
            " VALUES ('00000000-0000-0000-0000-000000000001', 'cfo', 5)",
        )
    )
    # 1. questions_used can never exceed the quota
    with pytest.raises(Exception, match="ck_team_stakeholder_state_quota"):
        _run(_sql(url, "UPDATE team_stakeholder_state SET questions_used = 6"))

    # 2. a stakeholder's department must exist in the stakeholder's own company
    with pytest.raises(Exception, match="fk_stakeholder_department"):
        _run(
            _sql(
                url,
                "INSERT INTO stakeholder (id, company_id, department, name, role, persona, canary)"
                " VALUES ('x', 'A', 'marketing', 'X', 'r', 'p', 'c')",
            )
        )
