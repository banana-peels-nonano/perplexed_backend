"""Alliances: teams from different companies pairing up.

Rules (from the organisers' checklist):
  - requests go only to teams of another company, and only while the current round allows
    alliances (capability "alliances");
  - an alliance locks for good once every member has confirmed; a team is in at most one
    locked alliance; accepting one withdraws that team's other open requests;
  - organisers can pair teams themselves (including one three-team alliance) and dissolve;
  - after the lock, partners see each other's evidence, and only partners do.

Teams (session cookie):
  GET    /alliance                     my alliance, incoming and outgoing requests
  GET    /alliance/teams               teams I could ask (other companies, not yet allied)
  POST   /alliance/requests            {"team": "T004"}
  POST   /alliance/requests/{id}/accept
  DELETE /alliance/requests/{id}       decline (if asked) or withdraw (if I asked)
  GET    /alliance/evidence            my partners' evidence, once locked

Organisers (bearer ADMIN_TOKEN):
  GET    /admin/alliances
  POST   /admin/alliances              {"teams": ["T001", "T004"]}  (2 or 3 teams), locked at once
  DELETE /admin/alliances/{id}         dissolve, even a locked one
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.admin import require_admin
from app.api.errors import ApiError
from app.api.interview import CaseDep
from app.api.portal import EvidenceItemOut, evidence_items
from app.auth.context import CurrentTeam, DbSession
from app.content.store import current_event
from app.models import Alliance, AllianceMember, Phase, Team, TeamDisclosure
from app.portal.alliance import partner_team_ids

teams = APIRouter(tags=["alliances"])
admin = APIRouter(prefix="/admin", tags=["organisers"], dependencies=[Depends(require_admin)])

MAX_OUTGOING = 3


class TeamRef(BaseModel):
    code: str
    name: str
    company_id: str | None


class RequestOut(BaseModel):
    id: uuid.UUID
    teams: list[TeamRef]  # the other team(s) in it
    created_at: datetime


class AllianceOut(BaseModel):
    can_request: bool  # the current round allows alliances
    locked: RequestOut | None
    incoming: list[RequestOut]
    outgoing: list[RequestOut]


async def _alliances_open(session) -> bool:
    event = await current_event(session)
    phase = await session.scalar(
        select(Phase).where(Phase.event_id == event.id, Phase.status == "running")
    )
    return bool(phase and phase.capabilities.get("alliances"))


async def _members(session, alliance_ids) -> dict[uuid.UUID, list[tuple[AllianceMember, Team]]]:
    out: dict = {}
    if not alliance_ids:
        return out
    rows = await session.execute(
        select(AllianceMember, Team)
        .join(Team, Team.id == AllianceMember.team_id)
        .where(AllianceMember.alliance_id.in_(alliance_ids))
        .order_by(Team.code)
    )
    for m, t in rows.all():
        out.setdefault(m.alliance_id, []).append((m, t))
    return out


def _ref(t: Team) -> TeamRef:
    return TeamRef(code=t.code, name=t.name, company_id=t.company_id)


async def _notify(request: Request, team_ids, *types: str) -> None:
    for team_id in set(team_ids):
        for t in types:
            await request.app.state.hub.publish(team_id, {"type": t})


async def _lock(session, alliance: Alliance, member_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Lock an alliance; withdraw every other open request involving its teams.
    Returns the teams whose requests were withdrawn (to notify them)."""
    alliance.status, alliance.locked_at = "locked", datetime.now(UTC)
    await session.execute(
        update(AllianceMember)
        .where(AllianceMember.alliance_id == alliance.id)
        .values(locked=True, confirmed_at=func.coalesce(AllianceMember.confirmed_at, func.now()))
    )
    others = select(AllianceMember.alliance_id).where(
        AllianceMember.team_id.in_(member_ids), AllianceMember.alliance_id != alliance.id
    )
    withdrawn = (
        await session.scalars(
            update(Alliance)
            .where(Alliance.id.in_(others), Alliance.status == "pending")
            .values(status="dissolved", dissolved_at=func.now())
            .returning(Alliance.id)
        )
    ).all()
    affected = await session.scalars(
        select(AllianceMember.team_id).where(AllianceMember.alliance_id.in_(withdrawn))
    )
    try:
        await session.flush()
    except IntegrityError as exc:  # someone got locked elsewhere a moment ago
        raise ApiError(
            409, "ALREADY_ALLIED", "one of these teams is already in an alliance"
        ) from exc
    return list(affected)


async def _locked_team_ids(session) -> set[uuid.UUID]:
    return set(await session.scalars(select(AllianceMember.team_id).where(AllianceMember.locked)))


# ------------------------------------------------------------------ teams
@teams.get("/alliance", response_model=AllianceOut)
async def my_alliance(team: CurrentTeam, session: DbSession) -> AllianceOut:
    mine = (
        await session.scalars(
            select(Alliance)
            .join(AllianceMember, AllianceMember.alliance_id == Alliance.id)
            .where(AllianceMember.team_id == team.team_id, Alliance.status != "dissolved")
            .order_by(Alliance.created_at)
        )
    ).all()
    members = await _members(session, [a.id for a in mine])
    locked, incoming, outgoing = None, [], []
    for a in mine:
        others = [t for m, t in members.get(a.id, []) if t.id != team.team_id]
        me = next(m for m, t in members[a.id] if t.id == team.team_id)
        out = RequestOut(id=a.id, teams=[_ref(t) for t in others], created_at=a.created_at)
        if a.status == "locked":
            locked = out
        elif me.confirmed_at is None:
            incoming.append(out)
        else:
            outgoing.append(out)
    return AllianceOut(
        can_request=await _alliances_open(session) and locked is None,
        locked=locked,
        incoming=incoming,
        outgoing=outgoing,
    )


@teams.get("/alliance/teams", response_model=list[TeamRef])
async def candidates(team: CurrentTeam, session: DbSession) -> list[TeamRef]:
    if team.company_id is None:
        return []
    taken = await _locked_team_ids(session)
    rows = await session.scalars(
        select(Team)
        .where(Team.company_id.is_not(None), Team.company_id != team.company_id)
        .order_by(Team.code)
    )
    return [_ref(t) for t in rows if t.id not in taken]


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str = Field(min_length=1, max_length=20)


@teams.post("/alliance/requests", response_model=RequestOut, status_code=201)
async def request_alliance(
    body: AskIn, team: CurrentTeam, session: DbSession, request: Request
) -> RequestOut:
    if not await _alliances_open(session):
        raise ApiError(409, "ALLIANCES_CLOSED", "alliances can't be formed in this round")
    if team.company_id is None:
        raise ApiError(409, "COMPANY_NOT_ASSIGNED", "your team has no company yet")
    target = await session.scalar(select(Team).where(Team.code == body.team.strip().upper()))
    if target is None or target.id == team.team_id:
        raise ApiError(404, "NOT_FOUND", "no such team")
    if target.company_id is None or target.company_id == team.company_id:
        raise ApiError(409, "SAME_COMPANY", "alliances are between teams of different companies")
    taken = await _locked_team_ids(session)
    if team.team_id in taken:
        raise ApiError(409, "ALREADY_ALLIED", "your team is already in an alliance")
    if target.id in taken:
        raise ApiError(409, "ALREADY_ALLIED", f"{target.code} is already in an alliance")
    open_ids = (
        select(AllianceMember.alliance_id)
        .join(Alliance, Alliance.id == AllianceMember.alliance_id)
        .where(AllianceMember.team_id == team.team_id, Alliance.status == "pending")
    )
    between = await session.scalar(
        select(func.count()).where(
            AllianceMember.alliance_id.in_(open_ids), AllianceMember.team_id == target.id
        )
    )
    if between:
        raise ApiError(409, "REQUEST_EXISTS", f"there's already an open request with {target.code}")
    mine_out = await session.scalar(
        select(func.count()).where(
            Alliance.status == "pending", Alliance.requested_by == team.team_id
        )
    )
    if mine_out >= MAX_OUTGOING:
        raise ApiError(409, "TOO_MANY_REQUESTS", f"at most {MAX_OUTGOING} open requests at a time")
    event = await current_event(session)
    a = Alliance(event_id=event.id, requested_by=team.team_id)
    session.add(a)
    await session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            AllianceMember(alliance_id=a.id, team_id=team.team_id, confirmed_at=now),
            AllianceMember(alliance_id=a.id, team_id=target.id),
        ]
    )
    await session.commit()
    await _notify(request, [team.team_id, target.id], "alliance")
    return RequestOut(id=a.id, teams=[_ref(target)], created_at=a.created_at)


async def _pending_for(session, alliance_id: uuid.UUID, team: CurrentTeam) -> Alliance:
    a = await session.get(Alliance, alliance_id)
    member = await session.get(AllianceMember, (alliance_id, team.team_id))
    if a is None or member is None or a.status == "dissolved":
        raise ApiError(404, "NOT_FOUND", "no such request")
    if a.status == "locked":
        raise ApiError(
            409, "ALLIANCE_LOCKED", "this alliance is locked; only organisers can undo it"
        )
    return a


@teams.post("/alliance/requests/{alliance_id}/accept", response_model=AllianceOut)
async def accept(
    alliance_id: uuid.UUID, team: CurrentTeam, session: DbSession, request: Request
) -> AllianceOut:
    if not await _alliances_open(session):
        raise ApiError(409, "ALLIANCES_CLOSED", "alliances can't be formed in this round")
    a = await _pending_for(session, alliance_id, team)
    if team.team_id in await _locked_team_ids(session):
        raise ApiError(409, "ALREADY_ALLIED", "your team is already in an alliance")
    member = await session.get(AllianceMember, (alliance_id, team.team_id))
    member.confirmed_at = member.confirmed_at or datetime.now(UTC)
    await session.flush()
    rows = (await _members(session, [a.id]))[a.id]
    ids = [t.id for _, t in rows]
    affected: list = []
    if all(m.confirmed_at for m, _ in rows):
        affected = await _lock(session, a, ids)
    await session.commit()
    await _notify(request, ids + affected, "alliance")
    if a.status == "locked":
        await _notify(request, ids, "evidence")  # partners' evidence is now theirs to see
    return await my_alliance(team, session)


@teams.delete("/alliance/requests/{alliance_id}", response_model=AllianceOut)
async def withdraw(
    alliance_id: uuid.UUID, team: CurrentTeam, session: DbSession, request: Request
) -> AllianceOut:
    a = await _pending_for(session, alliance_id, team)
    ids = [t.id for _, t in (await _members(session, [a.id]))[a.id]]
    a.status, a.dissolved_at = "dissolved", datetime.now(UTC)
    await session.commit()
    await _notify(request, ids, "alliance")
    return await my_alliance(team, session)


class PartnerEvidenceOut(EvidenceItemOut):
    team_code: str  # which partner team was shown it


@teams.get("/alliance/evidence", response_model=list[PartnerEvidenceOut])
async def partner_evidence(
    team: CurrentTeam, session: DbSession, case: CaseDep
) -> list[PartnerEvidenceOut]:
    partners = await partner_team_ids(session, team.team_id)
    if not partners:
        return []
    codes = dict(
        (await session.execute(select(Team.id, Team.code).where(Team.id.in_(partners)))).all()
    )
    rows = (
        await session.scalars(
            select(TeamDisclosure)
            .where(TeamDisclosure.team_id.in_(partners))
            .order_by(TeamDisclosure.disclosed_at, TeamDisclosure.passage_id)
        )
    ).all()
    rows = [d for d in rows if d.passage_id in case.passage]
    return [
        PartnerEvidenceOut(**item.model_dump(), team_code=codes[d.team_id])
        for d, item in zip(rows, evidence_items(case, rows), strict=True)
    ]


# ------------------------------------------------------------------ organisers
@admin.get("/alliances")
async def list_alliances(session: DbSession, include_dissolved: bool = False) -> list[dict]:
    stmt = select(Alliance).order_by(Alliance.created_at)
    if not include_dissolved:
        stmt = stmt.where(Alliance.status != "dissolved")
    rows = (await session.scalars(stmt)).all()
    members = await _members(session, [a.id for a in rows])
    return [
        {
            "id": a.id,
            "status": a.status,
            "by_organisers": a.by_organisers,
            "created_at": a.created_at,
            "locked_at": a.locked_at,
            "teams": [
                {
                    "code": t.code,
                    "name": t.name,
                    "company": t.company_id,
                    "confirmed": m.confirmed_at is not None,
                }  # fmt: skip
                for m, t in members.get(a.id, [])
            ],
        }
        for a in rows
    ]


class PairIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teams: list[str] = Field(min_length=2, max_length=3)


@admin.post("/alliances", status_code=201)
async def pair(body: PairIn, session: DbSession, request: Request) -> dict:
    codes = [c.strip().upper() for c in body.teams]
    found = (await session.scalars(select(Team).where(Team.code.in_(codes)))).all()
    if len(found) != len(set(codes)):
        missing = sorted(set(codes) - {t.code for t in found})
        raise ApiError(
            404, "NOT_FOUND", f"no team(s) {missing}" if missing else "a team is listed twice"
        )
    if any(t.company_id is None for t in found) or len({t.company_id for t in found}) < 2:
        raise ApiError(409, "SAME_COMPANY", "an alliance needs teams from at least two companies")
    taken = await _locked_team_ids(session)
    clash = sorted(t.code for t in found if t.id in taken)
    if clash:
        raise ApiError(409, "ALREADY_ALLIED", f"already in an alliance: {clash}")
    event = await current_event(session)
    a = Alliance(event_id=event.id, by_organisers=True)
    session.add(a)
    await session.flush()
    session.add_all([AllianceMember(alliance_id=a.id, team_id=t.id) for t in found])
    await session.flush()
    affected = await _lock(session, a, [t.id for t in found])
    await session.commit()
    ids = [t.id for t in found]
    await _notify(request, ids + affected, "alliance")
    await _notify(request, ids, "evidence")
    return {"id": a.id, "status": a.status, "teams": sorted(codes)}


@admin.delete("/alliances/{alliance_id}")
async def dissolve(alliance_id: uuid.UUID, session: DbSession, request: Request) -> dict:
    a = await session.get(Alliance, alliance_id)
    if a is None:
        raise ApiError(404, "NOT_FOUND", "no such alliance")
    ids = list(
        await session.scalars(
            select(AllianceMember.team_id).where(AllianceMember.alliance_id == alliance_id)
        )
    )
    a.status, a.dissolved_at = "dissolved", datetime.now(UTC)
    await session.execute(
        update(AllianceMember).where(AllianceMember.alliance_id == alliance_id).values(locked=False)
    )
    await session.commit()
    await _notify(request, ids, "alliance", "evidence")
    return {"id": alliance_id, "status": "dissolved"}
