"""Who is asking? Every team-facing endpoint depends on this.

Isolation rule (audit section 8): the team and its company come ONLY from the session
cookie. No team-facing endpoint accepts a team_id or company_id from the request, so a
team cannot ask for another team's or company's data even by editing a request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.auth.sessions import COOKIE_NAME, hash_token
from app.config import get_settings
from app.db import get_session
from app.models import Team, TeamSession

DbSession = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class TeamContext:
    team_id: uuid.UUID
    team_code: str
    company_id: str | None  # None until organisers assign the team to a company
    session_id: uuid.UUID


async def get_team_context(request: Request, session: DbSession) -> TeamContext:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise ApiError(401, "UNAUTHENTICATED", "log in first")
    team = await team_from_token(session, token)
    if team is None:
        raise ApiError(401, "UNAUTHENTICATED", "session expired or logged out; log in again")
    return team


async def team_from_token(session: AsyncSession, token: str) -> TeamContext | None:
    """The team behind a session token, or None (unknown, revoked or expired). Shared by
    HTTP requests and the WebSocket handshake."""
    cutoff = datetime.now(UTC) - timedelta(hours=get_settings().session_ttl_hours)
    row = (
        await session.execute(
            select(TeamSession.id, Team.id, Team.code, Team.company_id)
            .join(Team, Team.id == TeamSession.team_id)
            .where(
                TeamSession.token_hash == hash_token(token),
                TeamSession.revoked_at.is_(None),
                TeamSession.created_at > cutoff,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    session_id, team_id, team_code, company_id = row
    return TeamContext(team_id, team_code, company_id, session_id)


# Endpoints write `team: CurrentTeam` instead of repeating Depends(...) everywhere.
CurrentTeam = Annotated[TeamContext, Depends(get_team_context)]
