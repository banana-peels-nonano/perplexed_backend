"""GET /stakeholders and GET /stakeholders/{id}: the team's own company only.

Trust is hidden by the rules, so it never appears in any response. Questions remaining
does, and so does an HR lockout (a visible consequence of the team's own conduct). A
lockout covers the whole department, so every stakeholder in it shows the same countdown.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import and_, select

from app.api.errors import ApiError
from app.auth.context import CurrentTeam, DbSession, TeamContext
from app.models import Stakeholder, TeamDepartmentState, TeamStakeholderState

router = APIRouter(tags=["stakeholders"])


class StakeholderOut(BaseModel):
    id: str
    name: str
    role: str
    department: str
    questions_remaining: int
    # seconds until their department talks to the team again after an HR complaint
    # (0 = available); the same for everyone in the department
    available_in_seconds: int = 0
    # how the portrait is drawn; empty = picked from the id (content.schema.Look)
    look: dict = {}


def _query(team: TeamContext):
    # LEFT JOINs: a stakeholder with no state row yet still appears (0 remaining), and a
    # department the team hasn't questioned yet has no HR row
    tds = TeamDepartmentState
    return (
        select(Stakeholder, TeamStakeholderState, tds.locked_until)
        .outerjoin(
            TeamStakeholderState,
            and_(
                TeamStakeholderState.stakeholder_id == Stakeholder.id,
                TeamStakeholderState.team_id == team.team_id,
            ),
        )
        .outerjoin(
            tds,
            and_(
                tds.team_id == team.team_id,
                tds.company_id == Stakeholder.company_id,
                tds.department == Stakeholder.department,
            ),
        )
        .where(Stakeholder.company_id == team.company_id, Stakeholder.is_active.is_(True))
    )


def seconds_until(locked_until: datetime | None, now: datetime | None = None) -> int:
    """Whole seconds left on a lockout, rounded up; 0 when there is none or it has passed."""
    now = now or datetime.now(UTC)
    if locked_until is None or locked_until <= now:
        return 0
    return int((locked_until - now).total_seconds()) + 1


def _out(
    s: Stakeholder, state: TeamStakeholderState | None, locked_until: datetime | None = None
) -> StakeholderOut:
    remaining = (state.quota - state.questions_used) if state else 0
    locked = seconds_until(locked_until)
    return StakeholderOut(
        id=s.id,
        name=s.name,
        role=s.role,
        department=s.department,
        questions_remaining=remaining,
        available_in_seconds=locked,
        look=s.look or {},
    )


def _require_company(team: TeamContext) -> None:
    if team.company_id is None:
        raise ApiError(409, "COMPANY_NOT_ASSIGNED", "your team has not been assigned a company yet")


@router.get("/stakeholders", response_model=list[StakeholderOut])
async def list_stakeholders(team: CurrentTeam, session: DbSession) -> list[StakeholderOut]:
    _require_company(team)
    rows = (await session.execute(_query(team).order_by(Stakeholder.sort_order))).all()
    return [_out(*r) for r in rows]


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
