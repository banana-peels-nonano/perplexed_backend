"""What the portal needs besides interviews: team status, the evidence list, the board.

  GET /status     phase and time left, questions left per stakeholder, evidence count
  GET /evidence   every card this team has been shown, from any stakeholder
  GET /board      the team's evidence board
  PUT /board      save it (optimistic concurrency: send the version you edited)
  GET /brief          the case brief: the shared introduction and the team's company brief
  GET /announcements  organiser broadcasts for this team (everyone's, plus its company's)
  GET /display        no login: what the projector shows (phase, clock, public broadcasts)

Same rules as everywhere: team context comes only from the session cookie, and nothing
here carries trust, check results or anything the team hasn't been shown.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.errors import ApiError
from app.api.interview import CaseDep
from app.api.stakeholders import StakeholderOut, _out, _query, _require_company
from app.auth.context import CurrentTeam, DbSession
from app.content.schema import Mode
from app.content.store import current_event
from app.interview.render import render
from app.models import (
    Announcement,
    Company,
    Message,
    Phase,
    Stakeholder,
    Team,
    TeamBoard,
    TeamDisclosure,
)
from app.portal.board import BoardIn, stored
from app.portal.clock import interviews_open, not_in_round, phase_remaining, team_bonus

router = APIRouter(tags=["portal"])


class PhaseOut(BaseModel):
    key: str
    name: str
    status: str  # 'running' | 'paused'
    remaining_seconds: int | None  # None: no timer. Includes any time given back to this team
    interviews_open: bool


class TeamOut(BaseModel):
    code: str
    name: str
    company_id: str | None


class StatusOut(BaseModel):
    team: TeamOut
    phase: PhaseOut | None  # None between phases
    stakeholders: list[StakeholderOut]
    evidence_count: int
    server_time: datetime  # so a client can correct its own clock for the countdown


class EvidenceItemOut(BaseModel):
    passage_id: str
    record_id: str
    record_title: str
    record_date: str  # '' when the record has none
    excerpt: str  # the case's text at `mode` (paraphrase withholds figures)
    mode: str  # 'quote' | 'paraphrase'
    stakeholder_id: str  # who first disclosed it
    stakeholder_name: str
    discovered_at: datetime


class BoardOut(BaseModel):
    version: int
    cards: list[dict]
    links: list[dict]
    extra: dict
    updated_at: datetime | None


@router.get("/status", response_model=StatusOut)
async def status(team: CurrentTeam, session: DbSession) -> StatusOut:
    now = datetime.now(UTC)
    row = await session.get(Team, team.team_id)
    event = await current_event(session)
    phase = await session.scalar(
        select(Phase).where(Phase.event_id == event.id, Phase.status.in_(["running", "paused"]))
    )
    phase_out = None
    if phase is not None:
        bonus = await team_bonus(session, team.team_id, phase)  # time given back after a crash
        phase_out = PhaseOut(
            key=phase.key,
            name=phase.display_name,
            status=phase.status,
            remaining_seconds=phase_remaining(phase, now, bonus),
            interviews_open=interviews_open(phase, now, bonus)
            and not await not_in_round(session, team.team_id, phase),
        )
    stakeholders: list[StakeholderOut] = []
    if team.company_id is not None:
        rows = (await session.execute(_query(team).order_by(Stakeholder.sort_order))).all()
        stakeholders = [_out(*r) for r in rows]
    count = await session.scalar(
        select(func.count())
        .select_from(TeamDisclosure)
        .where(TeamDisclosure.team_id == team.team_id)
    )
    return StatusOut(
        team=TeamOut(code=row.code, name=row.name, company_id=row.company_id),
        phase=phase_out,
        stakeholders=stakeholders,
        evidence_count=count or 0,
        server_time=now,
    )


@router.get("/evidence", response_model=list[EvidenceItemOut])
async def evidence(team: CurrentTeam, session: DbSession, case: CaseDep) -> list[EvidenceItemOut]:
    _require_company(team)
    rows = (
        await session.scalars(
            select(TeamDisclosure)
            .where(TeamDisclosure.team_id == team.team_id)
            .order_by(TeamDisclosure.disclosed_at, TeamDisclosure.passage_id)
        )
    ).all()
    out = []
    for d in rows:
        if d.passage_id not in case.passage:  # content reloaded without it: skip, don't fail
            continue
        passage = case.passage[d.passage_id]
        record = case.record[passage.record]
        who = case.stakeholder.get(d.stakeholder_id)
        out.append(
            EvidenceItemOut(
                passage_id=d.passage_id,
                record_id=passage.record,
                record_title=record.title,
                record_date=record.date or "",
                excerpt=render(case, d.passage_id, Mode[d.mode]),
                mode=d.mode,
                stakeholder_id=d.stakeholder_id,
                stakeholder_name=who.name if who else "",
                discovered_at=d.disclosed_at,
            )
        )
    return out


def _board_out(row: TeamBoard | None) -> BoardOut:
    if row is None:
        return BoardOut(version=0, cards=[], links=[], extra={}, updated_at=None)
    doc = row.doc
    return BoardOut(
        version=row.version,
        cards=doc.get("cards", []),
        links=doc.get("links", []),
        extra=doc.get("extra", {}),
        updated_at=row.updated_at,
    )


@router.get("/board", response_model=BoardOut)
async def get_board(team: CurrentTeam, session: DbSession) -> BoardOut:
    return _board_out(await session.get(TeamBoard, team.team_id))


@router.put("/board", response_model=BoardOut)
async def put_board(
    body: BoardIn, team: CurrentTeam, session: DbSession, request: Request
) -> BoardOut:
    _require_company(team)
    # evidence cards: only passages this team has actually been shown
    shown = set(
        await session.scalars(
            select(TeamDisclosure.passage_id).where(TeamDisclosure.team_id == team.team_id)
        )
    )
    unknown = sorted({c.passage_id for c in body.cards if c.kind == "evidence"} - shown)
    if unknown:
        raise ApiError(422, "UNKNOWN_EVIDENCE", "the board refers to evidence your team "
                       "hasn't been shown", {"passage_ids": unknown})  # fmt: skip

    # quote cards: the server cuts the text from the team's own answered messages
    quote_ids = {c.message_id for c in body.cards if c.kind == "quote"}
    messages: dict[str, Message] = {}
    if quote_ids:
        try:
            wanted = [uuid.UUID(m) for m in quote_ids]
        except ValueError as exc:
            raise ApiError(422, "BAD_QUOTE", "message_id is not a valid id") from exc
        found = await session.scalars(
            select(Message).where(
                Message.id.in_(wanted),
                Message.team_id == team.team_id,
                Message.role == "answer",
                Message.status == "answered",
            )
        )
        messages = {str(m.id): m for m in found}
    for card in body.cards:
        if card.kind != "quote":
            continue
        message = messages.get(card.message_id)
        if message is None or card.end > len(message.text):
            raise ApiError(422, "BAD_QUOTE", f"card {card.id}: no such answer or span",
                           {"card_id": card.id})  # fmt: skip
        card.text = message.text[card.start : card.end]
        card.stakeholder_id = message.stakeholder_id

    doc = stored(body)
    if body.version == 0:
        # first save: if another laptop's first save got there first, this inserts nothing
        result = await session.execute(
            pg_insert(TeamBoard)
            .values(team_id=team.team_id, version=1, doc=doc)
            .on_conflict_do_nothing()
            .returning(TeamBoard.team_id)
        )
        saved = result.scalar_one_or_none() is not None
    else:
        result = await session.execute(
            update(TeamBoard)
            .where(TeamBoard.team_id == team.team_id, TeamBoard.version == body.version)
            .values(version=TeamBoard.version + 1, doc=doc, updated_at=func.now())
            .returning(TeamBoard.team_id)
        )
        saved = result.scalar_one_or_none() is not None
    if not saved:
        await session.rollback()
        current = await session.get(TeamBoard, team.team_id)
        raise ApiError(409, "BOARD_CONFLICT", "someone in your team saved the board first; "
                       "reload it and apply your change again",
                       {"current_version": current.version if current else 0})  # fmt: skip
    await session.commit()
    out = _board_out(await session.get(TeamBoard, team.team_id, populate_existing=True))
    await request.app.state.hub.publish(team.team_id, {"type": "board", "version": out.version})
    return out


# ------------------------------------------------------------------ brief and broadcasts
class CompanyBriefOut(BaseModel):
    id: str
    name: str
    brief: str


class BriefOut(BaseModel):
    case: str  # markdown, the same for every team
    company: CompanyBriefOut | None  # None until the team is assigned a company


@router.get("/brief", response_model=BriefOut)
async def brief(team: CurrentTeam, session: DbSession) -> BriefOut:
    event = await current_event(session)
    company = None
    if team.company_id is not None:
        row = await session.get(Company, team.company_id)
        company = CompanyBriefOut(id=row.id, name=row.name, brief=row.brief)
    return BriefOut(case=event.brief, company=company)


class AnnouncementOut(BaseModel):
    id: uuid.UUID
    text: str
    created_at: datetime


ANNOUNCEMENT_LIMIT = 50


def _live_announcements(event_id):
    return (
        select(Announcement)
        .where(Announcement.event_id == event_id, Announcement.retracted_at.is_(None))
        .order_by(Announcement.created_at.desc())
    )


@router.get("/announcements", response_model=list[AnnouncementOut])
async def announcements(team: CurrentTeam, session: DbSession) -> list[AnnouncementOut]:
    """Newest first. Company-only broadcasts reach that company's teams and nobody else."""
    event = await current_event(session)
    rows = await session.scalars(
        _live_announcements(event.id)
        .where(or_(Announcement.company_id.is_(None), Announcement.company_id == team.company_id))
        .limit(ANNOUNCEMENT_LIMIT)
    )
    return [_announcement_out(a) for a in rows]


def _announcement_out(a: Announcement) -> AnnouncementOut:
    return AnnouncementOut(id=a.id, text=a.text, created_at=a.created_at)


class DisplayOut(BaseModel):
    event: str
    phase: PhaseOut | None
    announcements: list[AnnouncementOut]  # everyone's broadcasts only, newest first
    server_time: datetime


@router.get("/display", response_model=DisplayOut)
async def display(session: DbSession) -> DisplayOut:
    """For the projector: no login, so nothing team- or company-specific is in it."""
    now = datetime.now(UTC)
    event = await current_event(session)
    phase = await session.scalar(
        select(Phase).where(Phase.event_id == event.id, Phase.status.in_(["running", "paused"]))
    )
    phase_out = None
    if phase is not None:
        phase_out = PhaseOut(
            key=phase.key,
            name=phase.display_name,
            status=phase.status,
            remaining_seconds=phase_remaining(phase, now),
            interviews_open=interviews_open(phase, now),
        )
    rows = await session.scalars(
        _live_announcements(event.id).where(Announcement.company_id.is_(None)).limit(5)
    )
    return DisplayOut(
        event=event.name,
        phase=phase_out,
        announcements=[_announcement_out(a) for a in rows],
        server_time=now,
    )
