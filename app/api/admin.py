"""Organiser API: run the event, watch it, fix it. Never reachable with a team session.

Every route needs "Authorization: Bearer <ADMIN_TOKEN>". With ADMIN_TOKEN unset the whole
API answers 404, as if it didn't exist.

  GET   /admin/overview                    the event at a glance
  GET   /admin/phases                      every phase, with time left
  POST  /admin/phases/{key}/start          start a phase (ends the current one)
  POST  /admin/phases/current/pause        freeze the clock and close interviews
  POST  /admin/phases/current/resume
  POST  /admin/phases/current/extend       {"seconds": 300} (negative shortens)
  POST  /admin/phases/current/end
  GET   /admin/teams                       every team: trust, HR, quota, clues, time, cost
  POST  /admin/teams                       {"names": [...]} register teams; codes shown once
  POST  /admin/teams/{code}/access-code    new access code (logs the team's laptops out)
  POST  /admin/teams/assign-companies      balanced random assignment
  PUT   /admin/teams/{code}/company        {"company": "B"} assign or move one team by hand
  POST  /admin/teams/{code}/time           {"seconds": 120} give one team time back
  GET   /admin/teams/{code}/threads/{sid}  a thread with everything the backend decided
  GET   /admin/teams/{code}/clues          which clues the team has found
  PATCH /admin/teams/{code}/stakeholders/{sid}   fix one team's state (trust, quota, lockout)
  GET   /admin/hr-flags                    every HR strike, warning and lockout
  GET   /admin/alerts                      prompt attacks and blocked answers, newest first
  POST  /admin/announcements               {"text": "...", "company"?: "A"} broadcast
  GET   /admin/announcements, DELETE /admin/announcements/{id}
  POST  /admin/quota                       {"quota": 12, "stakeholder_id"?: "cfo"} all teams
  GET   /admin/config, PATCH /admin/config live event settings
  POST  /admin/content/reload              {"path": "content/case.toml"}
  GET   /admin/costs                       model spend so far, recent provider errors

Participants must never reach any of this: it shows hidden trust and which answers were
deflections (the side channel the participant API refuses to carry).
"""

from __future__ import annotations

import hmac
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm.attributes import flag_modified

from app.api.errors import ApiError
from app.api.stakeholders import seconds_until
from app.auth.codes import generate_access_code, hash_access_code
from app.auth.context import DbSession
from app.config import get_settings
from app.content.loader import load_case
from app.content.store import current_event
from app.interview.service import tier_for
from app.models import (
    Announcement,
    Clue,
    Company,
    HrFlag,
    LlmCall,
    Message,
    Passage,
    Phase,
    Stakeholder,
    Team,
    TeamDepartmentState,
    TeamDisclosure,
    TeamSession,
    TeamStakeholderState,
    TeamTimeBonus,
)
from app.portal.clock import phase_remaining
from app.seed import ensure_team_state, seed_case

TSS = TeamStakeholderState
TDS = TeamDepartmentState
ROOT = Path(__file__).resolve().parents[2]
RELOADABLE = (ROOT / "content", ROOT / "tests" / "fixtures")


# HTTPBearer (rather than reading the header by hand) puts an "Authorize" button on /docs,
# so until there's an organiser screen, /docs works as an organiser console in a browser.
_bearer = HTTPBearer(auto_error=False, scheme_name="Organiser token", description="ADMIN_TOKEN")


def require_admin(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    request: Request,
) -> None:
    token = get_settings().admin_token
    if token is None:
        raise ApiError(404, "NOT_FOUND", "not found")  # no organiser API on this server
    if creds is None or not hmac.compare_digest(
        creds.credentials.encode(), token.get_secret_value().encode()
    ):
        raise ApiError(401, "ADMIN_UNAUTHENTICATED", "organiser token missing or wrong")
    request.state.actor = "admin"


router = APIRouter(prefix="/admin", tags=["organisers"], dependencies=[Depends(require_admin)])


# ------------------------------------------------------------------ phases
class PhaseAdminOut(BaseModel):
    key: str
    name: str
    seq: int
    status: str
    planned_seconds: int
    extended_seconds: int
    remaining_seconds: int | None
    capabilities: dict[str, Any]


def _phase_out(p: Phase, now: datetime) -> PhaseAdminOut:
    return PhaseAdminOut(
        key=p.key,
        name=p.display_name,
        seq=p.seq,
        status=p.status,
        planned_seconds=p.planned_seconds,
        extended_seconds=p.extended_seconds,
        remaining_seconds=phase_remaining(p, now),
        capabilities=p.capabilities,
    )


async def _phases(session) -> list[Phase]:
    event = await current_event(session)
    return list(
        (
            await session.scalars(
                select(Phase).where(Phase.event_id == event.id).order_by(Phase.seq)
            )
        ).all()
    )


async def _active(session) -> Phase:
    active = [p for p in await _phases(session) if p.status in ("running", "paused")]
    if not active:
        raise ApiError(409, "NO_ACTIVE_PHASE", "no phase is running or paused")
    return active[0]


@router.get("/phases", response_model=list[PhaseAdminOut])
async def phases(session: DbSession) -> list[PhaseAdminOut]:
    now = datetime.now(UTC)
    return [_phase_out(p, now) for p in await _phases(session)]


@router.post("/phases/{key}/start", response_model=PhaseAdminOut)
async def start(key: str, session: DbSession, request: Request) -> PhaseAdminOut:
    all_phases = await _phases(session)
    target = next((p for p in all_phases if p.key == key), None)
    if target is None:
        raise ApiError(404, "NOT_FOUND", f"no phase {key!r}")
    if target.status in ("running", "paused"):
        raise ApiError(409, "PHASE_ACTIVE", f"{key} is already {target.status}")
    for p in all_phases:  # one active phase at a time: starting the next ends the current
        if p.status in ("running", "paused"):
            p.status = "ended"
    await session.flush()  # the partial unique index must see the old one ended first
    target.status = "running"
    target.started_at = datetime.now(UTC)
    target.paused_at = None
    target.total_paused_seconds = 0
    await session.commit()
    # documents attached to this round become visible now
    await request.app.state.hub.broadcast({"type": "phase"})
    await request.app.state.hub.broadcast({"type": "documents"})
    return _phase_out(target, datetime.now(UTC))


@router.post("/phases/current/pause", response_model=PhaseAdminOut)
async def pause(session: DbSession, request: Request) -> PhaseAdminOut:
    p = await _active(session)
    if p.status == "paused":
        raise ApiError(409, "PHASE_PAUSED", "already paused")
    p.status, p.paused_at = "paused", datetime.now(UTC)
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    return _phase_out(p, datetime.now(UTC))


@router.post("/phases/current/resume", response_model=PhaseAdminOut)
async def resume(session: DbSession, request: Request) -> PhaseAdminOut:
    p = await _active(session)
    if p.status != "paused":
        raise ApiError(409, "PHASE_RUNNING", "not paused")
    now = datetime.now(UTC)
    p.total_paused_seconds += int((now - p.paused_at).total_seconds())
    p.status, p.paused_at = "running", None
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    return _phase_out(p, now)


class ExtendIn(BaseModel):
    seconds: int = Field(ge=-7200, le=7200)


@router.post("/phases/current/extend", response_model=PhaseAdminOut)
async def extend(body: ExtendIn, session: DbSession, request: Request) -> PhaseAdminOut:
    p = await _active(session)
    if p.planned_seconds == 0:
        raise ApiError(409, "PHASE_UNTIMED", "this phase has no timer to extend")
    p.extended_seconds = max(-p.planned_seconds, p.extended_seconds + body.seconds)
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    return _phase_out(p, datetime.now(UTC))


@router.post("/phases/current/end", response_model=PhaseAdminOut)
async def end(session: DbSession, request: Request) -> PhaseAdminOut:
    p = await _active(session)
    p.status, p.paused_at = "ended", None
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    return _phase_out(p, datetime.now(UTC))


# ------------------------------------------------------------------ teams
class StakeholderStateOut(BaseModel):
    stakeholder_id: str
    department: str
    trust: int
    tier: str
    strikes: int  # the department's (HR is per department)
    locked_for_seconds: int  # the department's lockout
    questions_used: int
    quota: int


class DepartmentStateOut(BaseModel):
    company_id: str
    department: str
    strikes: int
    locked_for_seconds: int


class TeamAdminOut(BaseModel):
    code: str
    name: str
    access_code: str | None  # None: made before codes were kept; rotate to get one
    company_id: str | None
    active_sessions: int
    evidence_count: int
    clues_found: int
    clues_total: int  # in the team's company
    questions_asked: int
    deflections: int
    hr_flags: int
    hr_walkouts: int
    time_bonus_seconds: int  # given back in the current phase
    cost_usd: float
    departments: list[DepartmentStateOut]
    stakeholders: list[StakeholderStateOut]


async def _clue_counts(session) -> tuple[dict, dict]:
    """({team_id: clues found}, {company_id: clues in the case}). A clue counts as found
    once the team has been shown any passage carrying it, paraphrased or quoted."""
    found = dict(
        (
            await session.execute(
                select(TeamDisclosure.team_id, func.count(func.distinct(Passage.clue_id)))
                .join(Passage, Passage.id == TeamDisclosure.passage_id)
                .where(Passage.clue_id.is_not(None))
                .group_by(TeamDisclosure.team_id)
            )
        ).all()
    )
    total = dict(
        (
            await session.execute(
                select(Passage.company_id, func.count(func.distinct(Passage.clue_id)))
                .where(Passage.clue_id.is_not(None))
                .group_by(Passage.company_id)
            )
        ).all()
    )
    return found, total


async def _count_by_team(session, stmt) -> dict:
    return dict((await session.execute(stmt)).all())


@router.get("/teams", response_model=list[TeamAdminOut])
async def teams(session: DbSession) -> list[TeamAdminOut]:
    now = datetime.now(UTC)
    event = await current_event(session)
    config = event.config
    cutoff = now.timestamp() - get_settings().session_ttl_hours * 3600

    sessions = await _count_by_team(
        session,
        select(TeamSession.team_id, func.count())
        .where(
            TeamSession.revoked_at.is_(None),
            func.extract("epoch", TeamSession.created_at) > cutoff,
        )
        .group_by(TeamSession.team_id),
    )
    evidence = await _count_by_team(
        session, select(TeamDisclosure.team_id, func.count()).group_by(TeamDisclosure.team_id)
    )
    flags = await _count_by_team(
        session, select(HrFlag.team_id, func.count()).group_by(HrFlag.team_id)
    )
    asked, deflected, walkouts = {}, {}, {}
    for team_id, role, status, n in (
        await session.execute(
            select(Message.team_id, Message.role, Message.status, func.count()).group_by(
                Message.team_id, Message.role, Message.status
            )
        )
    ).all():
        if role == "question" and status == "answered":
            asked[team_id] = asked.get(team_id, 0) + n
        if role == "answer" and status == "blocked":
            deflected[team_id] = deflected.get(team_id, 0) + n
        if role == "notice":
            walkouts[team_id] = walkouts.get(team_id, 0) + n
    costs = await _count_by_team(
        session, select(LlmCall.team_id, func.sum(LlmCall.cost_usd)).group_by(LlmCall.team_id)
    )
    clues_found, clues_total = await _clue_counts(session)
    active = await session.scalar(
        select(Phase).where(Phase.event_id == event.id, Phase.status.in_(["running", "paused"]))
    )
    bonuses = {}
    if active is not None:
        bonuses = await _count_by_team(
            session,
            select(TeamTimeBonus.team_id, TeamTimeBonus.seconds).where(
                TeamTimeBonus.phase_id == active.id
            ),
        )
    departments: dict = {}
    dept_state: dict = {}
    for d in (await session.scalars(select(TDS).order_by(TDS.department))).all():
        out = DepartmentStateOut(
            company_id=d.company_id,
            department=d.department,
            strikes=d.strikes,
            locked_for_seconds=seconds_until(d.locked_until, now),
        )
        departments.setdefault(d.team_id, []).append(out)
        dept_state[(d.team_id, d.company_id, d.department)] = out
    stakeholder_dept = dict(
        (await session.execute(select(Stakeholder.id, Stakeholder.department))).all()
    )
    stakeholder_company = dict(
        (await session.execute(select(Stakeholder.id, Stakeholder.company_id))).all()
    )
    states: dict = {}
    for st in (await session.scalars(select(TSS).order_by(TSS.stakeholder_id))).all():
        dept = stakeholder_dept.get(st.stakeholder_id, "")
        hr = dept_state.get((st.team_id, stakeholder_company.get(st.stakeholder_id), dept))
        states.setdefault(st.team_id, []).append(
            StakeholderStateOut(
                stakeholder_id=st.stakeholder_id,
                department=dept,
                trust=st.trust,
                tier=tier_for(st.trust, config["tier_thresholds"]).name,
                strikes=hr.strikes if hr else 0,
                locked_for_seconds=hr.locked_for_seconds if hr else 0,
                questions_used=st.questions_used,
                quota=st.quota,
            )
        )
    out = []
    for t in (await session.scalars(select(Team).order_by(Team.code))).all():
        out.append(
            TeamAdminOut(
                code=t.code,
                name=t.name,
                access_code=t.access_code,
                company_id=t.company_id,
                active_sessions=sessions.get(t.id, 0),
                evidence_count=evidence.get(t.id, 0),
                clues_found=clues_found.get(t.id, 0),
                clues_total=clues_total.get(t.company_id, 0),
                questions_asked=asked.get(t.id, 0),
                deflections=deflected.get(t.id, 0),
                hr_flags=flags.get(t.id, 0),
                hr_walkouts=walkouts.get(t.id, 0),
                time_bonus_seconds=bonuses.get(t.id, 0),
                cost_usd=float(costs.get(t.id) or 0),
                departments=departments.get(t.id, []),
                stakeholders=states.get(t.id, []),
            )
        )
    return out


async def _team(session, code: str) -> Team:
    team = await session.scalar(select(Team).where(Team.code == code))
    if team is None:
        raise ApiError(404, "NOT_FOUND", f"no team {code!r}")
    return team


class AdminMessageOut(BaseModel):
    seq: int
    role: str
    status: str
    text: str
    cited: list[str]
    meta: dict | None
    created_at: datetime


@router.get("/teams/{code}/threads/{stakeholder_id}", response_model=list[AdminMessageOut])
async def thread(code: str, stakeholder_id: str, session: DbSession) -> list[AdminMessageOut]:
    team = await _team(session, code)
    rows = await session.scalars(
        select(Message)
        .where(Message.team_id == team.id, Message.stakeholder_id == stakeholder_id)
        .order_by(Message.seq)
    )
    return [
        AdminMessageOut(
            seq=m.seq,
            role=m.role,
            status=m.status,
            text=m.text,
            cited=list(m.cited),
            meta=m.meta,
            created_at=m.created_at,
        )
        for m in rows
    ]


class StatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trust: int | None = Field(default=None, ge=0, le=100)
    quota: int | None = Field(default=None, ge=0, le=100)
    clear_lockout: bool = False


@router.patch("/teams/{code}/stakeholders/{stakeholder_id}", response_model=StakeholderStateOut)
async def patch_state(
    code: str, stakeholder_id: str, body: StatePatch, session: DbSession, request: Request
) -> StakeholderStateOut:
    team = await _team(session, code)
    request.state.target_team_id = str(team.id)
    state = await session.get(TSS, (team.id, stakeholder_id))
    if state is None:
        raise ApiError(404, "NOT_FOUND", f"team {code} has no stakeholder {stakeholder_id!r}")
    if body.trust is not None:
        state.trust = body.trust
    if body.quota is not None:
        state.quota = max(body.quota, state.questions_used)  # never below what's been used
    stakeholder = await session.get(Stakeholder, stakeholder_id)
    dept_where = (
        TDS.team_id == team.id,
        TDS.company_id == stakeholder.company_id,
        TDS.department == stakeholder.department,
    )
    if body.clear_lockout:  # clears the whole department: that is what the lockout covers
        await session.execute(
            update(TDS).where(*dept_where).values(strikes=0, locked_until=None, reset_at=func.now())
        )
    await session.commit()
    await request.app.state.hub.publish(team.id, {"type": "stakeholders"})
    config = (await current_event(session)).config
    hr = await session.scalar(select(TDS).where(*dept_where))
    return StakeholderStateOut(
        stakeholder_id=stakeholder_id,
        department=stakeholder.department,
        trust=state.trust,
        tier=tier_for(state.trust, config["tier_thresholds"]).name,
        strikes=hr.strikes if hr else 0,
        locked_for_seconds=seconds_until(hr.locked_until if hr else None),
        questions_used=state.questions_used,
        quota=state.quota,
    )


class QuotaIn(BaseModel):
    quota: int = Field(ge=0, le=100)
    stakeholder_id: str | None = None  # None: every stakeholder


@router.post("/quota")
async def set_quota(body: QuotaIn, session: DbSession, request: Request) -> dict[str, int]:
    stmt = update(TSS).values(quota=func.greatest(body.quota, TSS.questions_used))
    if body.stakeholder_id:
        stmt = stmt.where(TSS.stakeholder_id == body.stakeholder_id)
    result = await session.execute(stmt)
    await session.commit()
    await request.app.state.hub.broadcast({"type": "stakeholders"})
    return {"rows_updated": result.rowcount}


# ------------------------------------------------------------------ registration
class TeamsIn(BaseModel):
    names: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=300
    )
    prefix: str = Field(default="T", pattern=r"^[A-Z]{1,3}$")  # codes T001, T002, ...


class NewTeamOut(BaseModel):
    code: str
    name: str
    access_code: str


@router.post("/teams", response_model=list[NewTeamOut], status_code=201)
async def register_teams(body: TeamsIn, session: DbSession) -> list[NewTeamOut]:
    """Teams start without a company; assign them with /teams/assign-companies."""
    config = (await current_event(session)).config
    taken = set(await session.scalars(select(Team.code)))
    n, out = 0, []
    for name in body.names:
        n += 1
        while f"{body.prefix}{n:03d}" in taken:
            n += 1
        code = f"{body.prefix}{n:03d}"
        access_code = generate_access_code()
        session.add(
            Team(
                code=code,
                name=name.strip(),
                company_id=None,
                access_code_hash=hash_access_code(access_code),
                access_code=access_code,
                max_sessions=config.get("max_sessions_per_team", 2),
            )
        )
        out.append(NewTeamOut(code=code, name=name.strip(), access_code=access_code))
    await session.commit()
    return out


@router.post("/teams/{code}/access-code", response_model=NewTeamOut)
async def rotate_access_code(code: str, session: DbSession, request: Request) -> NewTeamOut:
    """A lost or leaked card: new code, and every laptop of the team is logged out."""
    team = await _team(session, code)
    request.state.target_team_id = str(team.id)
    access_code = generate_access_code()
    team.access_code_hash = hash_access_code(access_code)
    team.access_code = access_code
    revoked = (
        await session.scalars(
            update(TeamSession)
            .where(TeamSession.team_id == team.id, TeamSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(TeamSession.id)
        )
    ).all()
    await session.commit()
    for session_id in revoked:
        await request.app.state.hub.close_session(session_id)
    return NewTeamOut(code=team.code, name=team.name, access_code=access_code)


# ------------------------------------------------------------------ companies
async def _active_team_ids(session) -> set[uuid.UUID]:
    """Teams that have asked anything: moving them would orphan their threads/evidence."""
    return set(await session.scalars(select(Message.team_id).distinct()))


async def _set_company(session, team: Team, company_id: str | None) -> None:
    team.company_id = company_id
    await session.flush()
    # a team with no activity: drop state rows for any other company's stakeholders
    others = select(Stakeholder.id).where(Stakeholder.company_id != company_id)
    if company_id is None:
        others = select(Stakeholder.id)
    await session.execute(delete(TSS).where(TSS.team_id == team.id, TSS.stakeholder_id.in_(others)))
    await session.execute(delete(TDS).where(TDS.team_id == team.id))
    if company_id is not None:
        await ensure_team_state(session, team, (await current_event(session)).config)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # True: only teams without a company. False: re-deal every team that hasn't asked a
    # question yet (teams that have are kept where they are, and counted).
    only_unassigned: bool = True
    seed: int | None = None  # same seed, same draw: lets organisers rerun it publicly


@router.post("/teams/assign-companies")
async def assign_companies(body: AssignIn, session: DbSession, request: Request) -> dict:
    companies = sorted(await session.scalars(select(Company.id)))
    if not companies:
        raise ApiError(409, "NO_COMPANIES", "load a case first")
    all_teams = list((await session.scalars(select(Team).order_by(Team.code))).all())
    active = await _active_team_ids(session)
    if body.only_unassigned:
        movable = [t for t in all_teams if t.company_id is None]
    else:
        movable = [t for t in all_teams if t.id not in active]
    counts = {c: 0 for c in companies}
    for t in all_teams:
        if t not in movable and t.company_id in counts:
            counts[t.company_id] += 1
    rng = random.Random(body.seed)
    rng.shuffle(movable)
    assigned = {}
    for t in movable:
        fewest = min(counts.values())
        company = rng.choice([c for c in companies if counts[c] == fewest])
        counts[company] += 1
        await _set_company(session, t, company)
        assigned[t.code] = company
    await session.commit()
    for t in movable:
        await request.app.state.hub.publish(t.id, {"type": "team"}, {"type": "stakeholders"})
    return {
        "assigned": dict(sorted(assigned.items())),
        "teams_per_company": counts,
        "kept_because_active": sorted(t.code for t in all_teams if t.id in active),
    }


class CompanyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str | None  # None: unassign


@router.put("/teams/{code}/company")
async def set_team_company(
    code: str, body: CompanyIn, session: DbSession, request: Request
) -> dict:
    team = await _team(session, code)
    request.state.target_team_id = str(team.id)
    if body.company is not None and await session.get(Company, body.company) is None:
        raise ApiError(404, "NOT_FOUND", f"no company {body.company!r}")
    if team.company_id != body.company and team.id in await _active_team_ids(session):
        raise ApiError(
            409,
            "TEAM_HAS_ACTIVITY",
            f"{code} has already interviewed {team.company_id}'s stakeholders; moving it "
            "would leave its threads and evidence behind",
        )
    await _set_company(session, team, body.company)
    await session.commit()
    await request.app.state.hub.publish(team.id, {"type": "team"}, {"type": "stakeholders"})
    return {"code": team.code, "company_id": team.company_id}


# ------------------------------------------------------------------ time back
class TimeIn(BaseModel):
    seconds: int = Field(ge=-3600, le=3600)  # negative takes back an earlier grant


@router.post("/teams/{code}/time")
async def give_time(code: str, body: TimeIn, session: DbSession, request: Request) -> dict:
    """Extra time for one team in the current phase (their laptop crashed, the wifi died).
    Their countdown and interview gate move; nobody else's does."""
    team = await _team(session, code)
    request.state.target_team_id = str(team.id)
    phase = await _active(session)
    if phase.planned_seconds == 0:
        raise ApiError(409, "PHASE_UNTIMED", "this phase has no timer")
    stmt = pg_insert(TeamTimeBonus).values(
        team_id=team.id, phase_id=phase.id, seconds=max(0, body.seconds)
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TeamTimeBonus.team_id, TeamTimeBonus.phase_id],
        set_={"seconds": func.greatest(0, TeamTimeBonus.seconds + body.seconds)},
    ).returning(TeamTimeBonus.seconds)
    total = (await session.execute(stmt)).scalar_one()
    await session.commit()
    await request.app.state.hub.publish(team.id, {"type": "phase"})
    return {
        "code": team.code,
        "phase": phase.key,
        "time_bonus_seconds": total,
        "team_remaining_seconds": phase_remaining(phase, datetime.now(UTC), total),
    }


# ------------------------------------------------------------------ clues
@router.get("/teams/{code}/clues")
async def team_clues(code: str, session: DbSession) -> dict:
    """Judge material: which of its company's clues the team has uncovered, and how."""
    team = await _team(session, code)
    descriptions = dict((await session.execute(select(Clue.id, Clue.description))).all())
    company_clues: dict[str, list[str]] = {}
    for pid, clue in (
        await session.execute(
            select(Passage.id, Passage.clue_id).where(
                Passage.company_id == team.company_id, Passage.clue_id.is_not(None)
            )
        )
    ).all():
        company_clues.setdefault(clue, []).append(pid)
    shown = dict(
        (
            await session.execute(
                select(TeamDisclosure.passage_id, TeamDisclosure.mode).where(
                    TeamDisclosure.team_id == team.id
                )
            )
        ).all()
    )
    found, missing = [], []
    for clue, pids in sorted(company_clues.items()):
        via = {pid: shown[pid] for pid in sorted(pids) if pid in shown}
        item = {"clue": clue, "description": descriptions.get(clue, "")}
        if via:
            found.append(item | {"via": via})
        else:
            missing.append(item)
    return {"code": team.code, "company_id": team.company_id, "found": found, "missing": missing}


# ------------------------------------------------------------------ HR log and alerts
@router.get("/hr-flags")
async def hr_flags(session: DbSession, team: str | None = None, limit: int = 200) -> list[dict]:
    stmt = (
        select(HrFlag, Team.code, Message.text)
        .join(Team, Team.id == HrFlag.team_id)
        .join(Message, Message.id == HrFlag.message_id)
        .order_by(HrFlag.created_at.desc())
        .limit(min(max(limit, 1), 1000))
    )
    if team is not None:
        stmt = stmt.where(Team.code == team)
    return [
        {
            "at": f.created_at,
            "team": code,
            "stakeholder_id": f.stakeholder_id,
            "department": f.department,
            "label": f.label,
            "outcome": f.outcome,
            "question": text,
        }
        for f, code, text in (await session.execute(stmt)).all()
    ]


ALERT_LABELS = ("prompt_attack", "hostile")
NO_CHECKS = literal_column("'[]'::jsonb")


@router.get("/alerts")
async def alerts(session: DbSession, since: datetime | None = None, limit: int = 100) -> list[dict]:
    """Questions worth a human look, newest first: prompt attacks, hostility, and answers
    the checks blocked (the model tried to say something it mustn't). Poll with `since`."""
    meta = Message.meta
    stmt = (
        select(Message, Team.code)
        .join(Team, Team.id == Message.team_id)
        .where(
            Message.role == "question",
            meta.is_not(None),
            or_(
                meta["label"].astext.in_(ALERT_LABELS),
                meta["deflected"].astext == "true",
                func.jsonb_array_length(func.coalesce(meta["failed_checks"], NO_CHECKS)) > 0,
            ),
        )
        .order_by(Message.created_at.desc())
        .limit(min(max(limit, 1), 500))
    )
    if since is not None:
        stmt = stmt.where(Message.created_at > since)
    out = []
    for m, code in (await session.execute(stmt)).all():
        mm = m.meta or {}
        reasons = []
        if mm.get("label") in ALERT_LABELS:
            reasons.append(mm["label"])
        if mm.get("failed_checks"):
            reasons.append("blocked_draft")
        if mm.get("deflected"):
            reasons.append("deflected")
        out.append(
            {
                "at": m.created_at,
                "team": code,
                "stakeholder_id": m.stakeholder_id,
                "question": m.text,
                "reasons": reasons,
                "failed_checks": mm.get("failed_checks", []),
                "walked_out": mm.get("walked_out", False),
            }
        )
    return out


# ------------------------------------------------------------------ announcements
class AnnouncementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    company: str | None = None  # None: everyone, and the projector


def _announcement(a: Announcement) -> dict:
    return {
        "id": a.id,
        "text": a.text,
        "company": a.company_id,
        "created_at": a.created_at,
        "retracted_at": a.retracted_at,
    }


async def _notify_announcement(session, request: Request, company: str | None) -> None:
    if company is None:
        await request.app.state.hub.broadcast({"type": "announcement"})
        return
    for team_id in await session.scalars(select(Team.id).where(Team.company_id == company)):
        await request.app.state.hub.publish(team_id, {"type": "announcement"})


@router.post("/announcements", status_code=201)
async def announce(body: AnnouncementIn, session: DbSession, request: Request) -> dict:
    if body.company is not None and await session.get(Company, body.company) is None:
        raise ApiError(404, "NOT_FOUND", f"no company {body.company!r}")
    event = await current_event(session)
    a = Announcement(event_id=event.id, company_id=body.company, text=body.text.strip())
    session.add(a)
    await session.commit()
    await session.refresh(a)
    await _notify_announcement(session, request, body.company)
    return _announcement(a)


@router.get("/announcements")
async def list_announcements(session: DbSession) -> list[dict]:
    event = await current_event(session)
    rows = await session.scalars(
        select(Announcement)
        .where(Announcement.event_id == event.id)
        .order_by(Announcement.created_at.desc())
    )
    return [_announcement(a) for a in rows]


@router.delete("/announcements/{announcement_id}")
async def retract(announcement_id: uuid.UUID, session: DbSession, request: Request) -> dict:
    a = await session.get(Announcement, announcement_id)
    if a is None:
        raise ApiError(404, "NOT_FOUND", "no such announcement")
    if a.retracted_at is None:
        a.retracted_at = datetime.now(UTC)
        await session.commit()
        await _notify_announcement(session, request, a.company_id)
    return _announcement(a)


# ------------------------------------------------------------------ config
class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    neutral: int = Field(ge=0, le=100)
    open: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if not self.neutral < self.open:
            raise ValueError("neutral must be below open")
        return self


class HrRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strike_labels: list[str] | None = None
    strikes_to_lockout: int | None = Field(default=None, ge=1, le=20)
    lockout_seconds: int | None = Field(default=None, ge=0, le=3600)
    strike_scope: Literal["department", "stakeholder"] | None = None


class TrustRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deltas: dict[str, int] | None = None
    disrespect_penalty: int | None = Field(default=None, ge=0, le=50)
    max_gain: int | None = Field(default=None, ge=0, le=50)
    max_loss: int | None = Field(default=None, ge=0, le=100)


class Models(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str | None = None
    classify: str | None = None


class RegistrationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: bool | None = None
    min_members: int | None = Field(default=None, ge=1, le=10)
    max_members: int | None = Field(default=None, ge=1, le=10)
    domain: str | None = Field(default=None, pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")


class RecordsTab(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "titles", "full"]
    all_companies: bool = False  # true: every team sees every company's records


class ConfigPatch(BaseModel):
    """Only settings that are safe to change while the event runs. trust_start and
    default_quota apply to teams created afterwards; use /quota for existing teams."""

    model_config = ConfigDict(extra="forbid")
    tier_thresholds: Thresholds | None = None
    trust_rules: TrustRules | None = None
    hr: HrRules | None = None
    evidence_token_budget: int | None = Field(default=None, ge=200, le=4000)
    max_passages: int | None = Field(default=None, ge=1, le=8)
    models: Models | None = None
    trust_start: int | None = Field(default=None, ge=0, le=100)
    default_quota: int | None = Field(default=None, ge=0, le=100)
    registration: RegistrationRules | None = None
    records_tab: RecordsTab | None = None


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _merge(out.get(k) or {}, v) if isinstance(v, dict) else v
    return out


@router.get("/config")
async def get_config(session: DbSession) -> dict[str, Any]:
    return (await current_event(session)).config


@router.patch("/config")
async def patch_config(body: ConfigPatch, session: DbSession, request: Request) -> dict[str, Any]:
    event = await current_event(session)
    event.config = _merge(event.config, body.model_dump(exclude_none=True))
    flag_modified(event, "config")
    await session.commit()
    if body.records_tab is not None:  # every open portal shows or hides the tab now
        await request.app.state.hub.broadcast({"type": "records"})
    return event.config


# ------------------------------------------------------------------ content
class ReloadIn(BaseModel):
    path: str  # relative to the repo root, under content/ or tests/fixtures/


@router.post("/content/reload")
async def reload_content(body: ReloadIn, request: Request, session: DbSession) -> dict[str, Any]:
    path = (ROOT / body.path).resolve()
    if path.suffix != ".toml" or not any(path.is_relative_to(d) for d in RELOADABLE):
        raise ApiError(
            422, "BAD_PATH", "path must be a .toml file under content/ or tests/fixtures/"
        )
    if not path.is_file():
        raise ApiError(404, "NOT_FOUND", f"no file {body.path}")
    try:
        case = load_case(path)
    except ValueError as exc:  # the loader names every problem; pass them on
        raise ApiError(
            422, "INVALID_CASE", "the case file has problems", {"problems": str(exc)}
        ) from exc
    await seed_case(session, case)
    event = await current_event(session)
    event.brief = case.brief
    for team in (await session.scalars(select(Team))).all():
        await ensure_team_state(session, team, event.config)  # new stakeholders get rows
    await session.commit()
    request.app.state.content.invalidate()  # next request loads the new content
    await request.app.state.hub.broadcast({"type": "content"})
    return {
        "case_id": case.case_id,
        "stakeholders": len(case.stakeholder),
        "passages": len(case.passage),
    }


# ------------------------------------------------------------------ overview and costs
@router.get("/costs")
async def costs(session: DbSession) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                LlmCall.purpose,
                LlmCall.model,
                LlmCall.status,
                func.count(),
                func.sum(LlmCall.cost_usd),
                func.avg(LlmCall.latency_ms),
            ).group_by(LlmCall.purpose, LlmCall.model, LlmCall.status)
        )
    ).all()
    errors = (
        await session.execute(
            select(LlmCall.created_at, LlmCall.error)
            .where(LlmCall.status == "error")
            .order_by(LlmCall.created_at.desc())
            .limit(10)
        )
    ).all()
    return {
        "total_usd": float(sum(r[4] or 0 for r in rows)),
        "by_call": [
            {
                "purpose": p,
                "model": m,
                "status": s,
                "calls": n,
                "usd": float(c or 0),
                "avg_ms": round(float(ms or 0)),
            }  # fmt: skip
            for p, m, s, n, c, ms in rows
        ],
        "recent_errors": [{"at": at, "error": e} for at, e in errors],
    }


@router.get("/live")
async def live_connections(request: Request) -> dict[str, int]:
    """How many tabs are connected for live updates (single-process registry)."""
    return {"connections": request.app.state.hub.count()}


@router.get("/overview")
async def overview(session: DbSession) -> dict[str, Any]:
    now = datetime.now(UTC)
    event = await current_event(session)
    active = [p for p in await _phases(session) if p.status in ("running", "paused")]
    teams_n = await session.scalar(select(func.count()).select_from(Team))
    asked = await session.scalar(
        select(func.count()).where(Message.role == "question", Message.status == "answered")
    )
    deflected = await session.scalar(
        select(func.count()).where(Message.role == "answer", Message.status == "blocked")
    )
    locked = await session.scalar(
        select(func.count()).select_from(TDS).where(TDS.locked_until > func.now())
    )
    spent = await session.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0)))
    errors_10m = await session.scalar(
        select(func.count()).where(
            LlmCall.status == "error",
            LlmCall.created_at > func.now() - func.make_interval(0, 0, 0, 0, 0, 10),
        )
    )
    return {
        "event": event.name,
        "case_id": event.case_id,
        "phase": _phase_out(active[0], now).model_dump() if active else None,
        "teams": teams_n,
        "questions_asked": asked,
        "deflections": deflected,
        "active_lockouts": locked,
        "spent_usd": float(spent or 0),
        "provider_errors_last_10_min": errors_10m,
    }
