"""Login sessions: one row per logged-in laptop, identified by a random token in a cookie.

The browser holds the token; the database holds only its hash (like access codes).
A session is just a key to the team: all game state belongs to the team, so every
laptop of a team sees the same thing.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.auth.codes import hash_access_code
from app.models import Team, TeamSession

COOKIE_NAME = "pxd_session"


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def login(
    session: AsyncSession, access_code: str, device_label: str | None, ttl_hours: int
) -> tuple[Team, str]:
    """Check the code, enforce the per-team session cap, create a session. Returns (team, token).

    The team row is locked (SELECT ... FOR UPDATE) while counting its sessions. Without the
    lock, three laptops logging in at the same instant could each count 1 and all succeed.
    """
    team = await session.scalar(
        select(Team).where(Team.access_code_hash == hash_access_code(access_code)).with_for_update()
    )
    if team is None:
        raise ApiError(401, "INVALID_CODE", "that access code is not valid")

    now = datetime.now(UTC)
    active = await session.scalar(
        select(func.count())
        .select_from(TeamSession)
        .where(
            TeamSession.team_id == team.id,
            TeamSession.revoked_at.is_(None),
            TeamSession.created_at > now - timedelta(hours=ttl_hours),
        )
    )
    if active >= team.max_sessions:
        raise ApiError(
            409,
            "SESSION_LIMIT",
            f"this team already has {active} laptops logged in; log one out first",
            {"max_sessions": team.max_sessions},
        )

    token = new_token()
    session.add(
        TeamSession(team_id=team.id, token_hash=hash_token(token), device_label=device_label)
    )
    await session.commit()  # also releases the row lock
    return team, token


async def logout(session: AsyncSession, session_id) -> None:
    row = await session.get(TeamSession, session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
