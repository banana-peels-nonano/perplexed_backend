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
  GET   /admin/teams                       every team: trust, strikes, lockouts, quota, cost
  GET   /admin/teams/{code}/threads/{sid}  a thread with everything the backend decided
  PATCH /admin/teams/{code}/stakeholders/{sid}   fix one team's state (trust, quota, lockout)
  POST  /admin/quota                       {"quota": 12, "stakeholder_id"?: "cfo"} all teams
  GET   /admin/config, PATCH /admin/config live event settings
  POST  /admin/content/reload              {"path": "content/case.toml"}
  GET   /admin/costs                       model spend so far, recent provider errors

Participants must never reach any of this: it shows hidden trust and which answers were
deflections (the side channel the participant API refuses to carry).
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm.attributes import flag_modified

from app.api.errors import ApiError
from app.auth.context import DbSession
from app.config import get_settings
from app.content.loader import load_case
from app.content.store import current_event
from app.interview.service import tier_for
from app.models import (
    LlmCall,
    Message,
    Phase,
    Team,
    TeamDisclosure,
    TeamSession,
    TeamStakeholderState,
)
from app.portal.clock import phase_remaining
from app.seed import ensure_team_state, seed_case

TSS = TeamStakeholderState
ROOT = Path(__file__).resolve().parents[2]
RELOADABLE = (ROOT / "content", ROOT / "tests" / "fixtures")


def require_admin(request: Request) -> None:
    token = get_settings().admin_token
    if token is None:
        raise ApiError(404, "NOT_FOUND", "not found")  # no organiser API on this server
    sent = request.headers.get("authorization", "")
    scheme, _, value = sent.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        value.encode(), token.get_secret_value().encode()
    ):
        raise ApiError(401, "ADMIN_UNAUTHENTICATED", "organiser token missing or wrong")


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
    await request.app.state.hub.broadcast({"type": "phase"})
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
    trust: int
    tier: str
    strikes: int
    locked_for_seconds: int
    questions_used: int
    quota: int


class TeamAdminOut(BaseModel):
    code: str
    name: str
    company_id: str | None
    active_sessions: int
    evidence_count: int
    questions_asked: int
    deflections: int
    hr_walkouts: int
    cost_usd: float
    stakeholders: list[StakeholderStateOut]


def _locked_for(state: TSS, now: datetime) -> int:
    if state.locked_until is None or state.locked_until <= now:
        return 0
    return int((state.locked_until - now).total_seconds()) + 1


@router.get("/teams", response_model=list[TeamAdminOut])
async def teams(session: DbSession) -> list[TeamAdminOut]:
    now = datetime.now(UTC)
    config = (await current_event(session)).config
    cutoff = now.timestamp() - get_settings().session_ttl_hours * 3600

    sessions = dict(
        (
            await session.execute(
                select(TeamSession.team_id, func.count())
                .where(
                    TeamSession.revoked_at.is_(None),
                    func.extract("epoch", TeamSession.created_at) > cutoff,
                )
                .group_by(TeamSession.team_id)
            )
        ).all()
    )
    evidence = dict(
        (
            await session.execute(
                select(TeamDisclosure.team_id, func.count()).group_by(TeamDisclosure.team_id)
            )
        ).all()
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
    costs = dict(
        (
            await session.execute(
                select(LlmCall.team_id, func.sum(LlmCall.cost_usd)).group_by(LlmCall.team_id)
            )
        ).all()
    )
    states: dict = {}
    for st in (await session.scalars(select(TSS).order_by(TSS.stakeholder_id))).all():
        states.setdefault(st.team_id, []).append(
            StakeholderStateOut(
                stakeholder_id=st.stakeholder_id,
                trust=st.trust,
                tier=tier_for(st.trust, config["tier_thresholds"]).name,
                strikes=st.strikes,
                locked_for_seconds=_locked_for(st, now),
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
                company_id=t.company_id,
                active_sessions=sessions.get(t.id, 0),
                evidence_count=evidence.get(t.id, 0),
                questions_asked=asked.get(t.id, 0),
                deflections=deflected.get(t.id, 0),
                hr_walkouts=walkouts.get(t.id, 0),
                cost_usd=float(costs.get(t.id) or 0),
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
    state = await session.get(TSS, (team.id, stakeholder_id))
    if state is None:
        raise ApiError(404, "NOT_FOUND", f"team {code} has no stakeholder {stakeholder_id!r}")
    if body.trust is not None:
        state.trust = body.trust
    if body.quota is not None:
        state.quota = max(body.quota, state.questions_used)  # never below what's been used
    if body.clear_lockout:
        state.locked_until, state.strikes = None, 0
    await session.commit()
    await request.app.state.hub.publish(team.id, {"type": "stakeholders"})
    config = (await current_event(session)).config
    return StakeholderStateOut(
        stakeholder_id=stakeholder_id,
        trust=state.trust,
        tier=tier_for(state.trust, config["tier_thresholds"]).name,
        strikes=state.strikes,
        locked_for_seconds=_locked_for(state, datetime.now(UTC)),
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


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _merge(out.get(k) or {}, v) if isinstance(v, dict) else v
    return out


@router.get("/config")
async def get_config(session: DbSession) -> dict[str, Any]:
    return (await current_event(session)).config


@router.patch("/config")
async def patch_config(body: ConfigPatch, session: DbSession) -> dict[str, Any]:
    event = await current_event(session)
    event.config = _merge(event.config, body.model_dump(exclude_none=True))
    flag_modified(event, "config")
    await session.commit()
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
        select(func.count()).select_from(TSS).where(TSS.locked_until > func.now())
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
