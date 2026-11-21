"""Removing a team that hasn't played (a typo, a duplicate). Organisers only.

Teams are created by organisers in the console (POST /admin/teams) and their access
codes are read out to them in person: there is no self-registration and no email.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.admin import require_admin
from app.api.errors import ApiError
from app.auth.context import DbSession
from app.models import (
    EmailLog,
    Message,
    Team,
    TeamDepartmentState,
    TeamMember,
    TeamSession,
    TeamStakeholderState,
)

admin = APIRouter(prefix="/admin", tags=["organisers"], dependencies=[Depends(require_admin)])


@admin.delete("/teams/{code}")
async def delete_team(code: str, session: DbSession, request: Request) -> dict:
    team = await session.scalar(select(Team).where(Team.code == code))
    if team is None:
        raise ApiError(404, "NOT_FOUND", f"no team {code!r}")
    if await session.scalar(select(func.count()).where(Message.team_id == team.id)):
        raise ApiError(
            409, "TEAM_HAS_ACTIVITY", "this team has already played; it can't be removed"
        )
    sessions = (
        await session.scalars(select(TeamSession.id).where(TeamSession.team_id == team.id))
    ).all()
    for model in (TeamMember, EmailLog, TeamStakeholderState, TeamDepartmentState, TeamSession):
        await session.execute(delete(model).where(model.team_id == team.id))
    await session.delete(team)
    try:
        await session.commit()
    except IntegrityError as exc:  # board, alliance, results... something still points at it
        await session.rollback()
        raise ApiError(
            409, "TEAM_HAS_ACTIVITY", "this team has data attached; it can't be removed"
        ) from exc
    for sid in sessions:
        await request.app.state.hub.close_session(sid)
    return {"deleted": code}
