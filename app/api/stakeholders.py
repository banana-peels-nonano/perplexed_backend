"""GET /stakeholders and GET /stakeholders/{id}: the team's own company only.

Trust is hidden by the rules, so it never appears in any response. Questions remaining
does, and so does an HR lockout (a visible consequence of the team's own conduct).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import and_, select

from app.api.errors import ApiError
from app.auth.context import CurrentTeam, DbSession, TeamContext
from app.models import Stakeholder, TeamStakeholderState

router = APIRouter(tags=["stakeholders"])


class StakeholderOut(BaseModel):
    id: str
    name: str
    role: str
    department: str
    questions_remaining: int
    # seconds until they'll talk to the team again after an HR walk-out (0 = available)
    available_in_seconds: int = 0


def _query(team: TeamContext):
    # LEFT JOIN: a stakeholder with no state row yet still appears (0 remaining)
    return (
        select(Stakeholder, TeamStakeholderState)
        .outerjoin(
            TeamStakeholderState,
            and_(
                TeamStakeholderState.stakeholder_id == Stakeholder.id,
                TeamStakeholderState.team_id == team.team_id,
            ),
        )
        .where(Stakeholder.company_id == team.company_id, Stakeholder.is_active.is_(True))
    )


def _out(s: Stakeholder, state: TeamStakeholderState | None) -> StakeholderOut:
    remaining = (state.quota - state.questions_used) if state else 0
    locked = 0
    if state is not None and state.locked_until is not None:
        locked = max(0, int((state.locked_until - datetime.now(UTC)).total_seconds()) + 1)
        if state.locked_until <= datetime.now(UTC):
            locked = 0
    return StakeholderOut(
        id=s.id,
        name=s.name,
        role=s.role,
        department=s.department,
        questions_remaining=remaining,
        available_in_seconds=locked,
    )


def _require_company(team: TeamContext) -> None:
    if team.company_id is None:
        raise ApiError(409, "COMPANY_NOT_ASSIGNED", "your team has not been assigned a company yet")


@router.get("/stakeholders", response_model=list[StakeholderOut])
async def list_stakeholders(team: CurrentTeam, session: DbSession) -> list[StakeholderOut]:
    _require_company(team)
    rows = (await session.execute(_query(team).order_by(Stakeholder.sort_order))).all()
    return [_out(s, st) for s, st in rows]


@router.get("/stakeholders/{stakeholder_id}", response_model=StakeholderOut)
async def get_stakeholder(
    stakeholder_id: str, team: CurrentTeam, session: DbSession
) -> StakeholderOut:
    _require_company(team)
    row = (
        await session.execute(_query(team).where(Stakeholder.id == stakeholder_id))
    ).one_or_none()
    if row is None:
        # 404, not 403: another company's stakeholder looks exactly like one that doesn't
        # exist, so the response doesn't even confirm it's there
        raise ApiError(404, "NOT_FOUND", "no such stakeholder")
    return _out(*row)
