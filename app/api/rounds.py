"""Rounds, the documents handed out in them, and each team's status per round.

A round is a phase row: organisers add, rename, time, reorder and delete them here, so
the event isn't limited to the phases the case file named (built for 4, fine with more).

Organisers (bearer ADMIN_TOKEN):
  POST   /admin/phases                       add a round (at the end, or after one)
  PATCH  /admin/phases/{key}                 rename, retime, change what it allows
  PUT    /admin/phases/order                 {"keys": [...]} the full running order
  DELETE /admin/phases/{key}                 only a round that never started
  POST   /admin/documents                    a markdown note or a file (base64), for
                                             everyone, one company, or named teams
  GET    /admin/documents, PATCH/DELETE /admin/documents/{id}
  GET    /admin/companies                    ids and names, for the console's pickers
  GET    /admin/phases/{key}/statuses        every team's status for that round
  PUT    /admin/phases/{key}/statuses        set statuses (drafts unless "publish": true)
  POST   /admin/phases/{key}/statuses/publish   release every draft for that round at once

Teams (session cookie):
  GET /rounds              every round in order, with this team's published status
  GET /documents           what the team has been handed
  GET /documents/{id}      a note as JSON, or the file itself
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, any_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import undefer

from app.api.admin import _phase_out, _phases, require_admin
from app.api.errors import ApiError
from app.auth.context import CurrentTeam, DbSession
from app.content.store import current_event
from app.models import (
    Company,
    Document,
    Phase,
    StakeholderPassageAccess,
    Team,
    TeamRoundStatus,
    TeamTimeBonus,
)

admin = APIRouter(prefix="/admin", tags=["organisers"], dependencies=[Depends(require_admin)])
teams = APIRouter(tags=["rounds"])

KEY = r"^[a-z0-9][a-z0-9_]{0,39}$"
CAPABILITIES = {"interview", "alliances", "submissions"}
MAX_FILE_BYTES = 15 * 1024 * 1024
RESERVED = {"current", "order"}  # path words in /admin/phases/...


# ------------------------------------------------------------------ rounds
def _check_caps(caps: dict[str, bool] | None) -> dict[str, bool] | None:
    if caps is not None and not set(caps) <= CAPABILITIES:
        raise ValueError(f"capabilities are {sorted(CAPABILITIES)}")
    return caps


class PhaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=KEY)
    name: str = Field(min_length=1, max_length=60)
    planned_seconds: int = Field(default=0, ge=0, le=86_400)  # 0 = no timer
    capabilities: dict[str, bool] = Field(default_factory=lambda: {"interview": True})
    after: str | None = None  # key of the round it follows; None = at the end

    _caps = field_validator("capabilities")(_check_caps)


class PhasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=60)
    planned_seconds: int | None = Field(default=None, ge=0, le=86_400)
    capabilities: dict[str, bool] | None = None

    _caps = field_validator("capabilities")(_check_caps)


class OrderIn(BaseModel):
    keys: list[str] = Field(min_length=1)


async def _resequence(session, ordered: list[Phase]) -> None:
    """Write seq 0..n-1 in two steps: (event_id, seq) is unique, so swapping two rounds
    directly would collide halfway."""
    for p in ordered:
        p.seq += 10_000
    await session.flush()
    for i, p in enumerate(ordered):
        p.seq = i
    await session.flush()


async def _round(session, key: str) -> Phase:
    found = next((p for p in await _phases(session) if p.key == key), None)
    if found is None:
        raise ApiError(404, "NOT_FOUND", f"no round {key!r}")
    return found


@admin.post("/phases", status_code=201)
async def add_round(body: PhaseIn, session: DbSession, request: Request):
    event = await current_event(session)
    ordered = await _phases(session)
    if body.key in RESERVED:
        raise ApiError(422, "BAD_KEY", f"{body.key!r} is reserved")
    if any(p.key == body.key for p in ordered):
        raise ApiError(409, "PHASE_EXISTS", f"there is already a round {body.key!r}")
    at = len(ordered)
    if body.after is not None:
        keys = [p.key for p in ordered]
        if body.after not in keys:
            raise ApiError(404, "NOT_FOUND", f"no round {body.after!r}")
        at = keys.index(body.after) + 1
    new = Phase(
        event_id=event.id,
        key=body.key,
        display_name=body.name,
        seq=10_000 + len(ordered),  # out of the way until resequenced
        planned_seconds=body.planned_seconds,
        capabilities=body.capabilities,
    )
    session.add(new)
    await session.flush()
    ordered.insert(at, new)
    await _resequence(session, ordered)
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    return _phase_out(new, datetime.now(UTC))


@admin.patch("/phases/{key}")
async def edit_round(key: str, body: PhasePatch, session: DbSession, request: Request):
    p = await _round(session, key)
    if body.name is not None:
        p.display_name = body.name
    if body.planned_seconds is not None:
        p.planned_seconds = body.planned_seconds
    if body.capabilities is not None:
        p.capabilities = {**p.capabilities, **body.capabilities}
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    return _phase_out(p, datetime.now(UTC))


@admin.put("/phases/order")
async def reorder_rounds(body: OrderIn, session: DbSession, request: Request):
    ordered = await _phases(session)
    by_key = {p.key: p for p in ordered}
    if sorted(body.keys) != sorted(by_key):
        raise ApiError(
            422, "BAD_ORDER", "list every round exactly once", {"rounds": sorted(by_key)}
        )
    await _resequence(session, [by_key[k] for k in body.keys])
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    now = datetime.now(UTC)
    return [_phase_out(by_key[k], now) for k in body.keys]


@admin.delete("/phases/{key}")
async def delete_round(key: str, session: DbSession, request: Request):
    p = await _round(session, key)
    if p.started_at is not None or p.status != "scheduled":
        raise ApiError(409, "PHASE_USED", "a round that has started can't be deleted")
    unlocks = select(func.count()).where(StakeholderPassageAccess.phase_from == key)
    if await session.scalar(unlocks):
        raise ApiError(409, "PHASE_USED", f"the case unlocks evidence in {key!r}")
    for model, col in (
        (Document, Document.phase_id),
        (TeamRoundStatus, TeamRoundStatus.phase_id),
        (TeamTimeBonus, TeamTimeBonus.phase_id),
    ):
        if await session.scalar(select(func.count()).select_from(model).where(col == p.id)):
            raise ApiError(409, "PHASE_USED", f"{key!r} still has {model.__tablename__} rows")
    await session.delete(p)
    await session.flush()
    await _resequence(session, [x for x in await _phases(session) if x.id != p.id])
    await session.commit()
    await request.app.state.hub.broadcast({"type": "phase"})
    return {"deleted": key}


@admin.get("/companies")
async def companies(session: DbSession) -> list[dict]:
    rows = await session.execute(select(Company.id, Company.name).order_by(Company.id))
    return [{"id": cid, "name": name} for cid, name in rows.all()]


# ------------------------------------------------------------------ documents
class FileIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    data_base64: str


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=200_000)  # markdown
    file: FileIn | None = None
    round: str | None = None  # released automatically when this round starts
    company: str | None = None  # None = every company
    teams: list[str] | None = None  # team codes; None = every team (in that company)
    release_now: bool = False


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    released: bool | None = None  # true: release now; false: withdraw


def _visible_to(team: CurrentTeam):
    """SQL condition: released (by hand, or by its round starting) and meant for this team."""
    return and_(
        or_(Document.released_at.is_not(None), Phase.started_at.is_not(None)),
        or_(Document.company_id.is_(None), Document.company_id == team.company_id),
        or_(Document.team_ids.is_(None), literal(team.team_id) == any_(Document.team_ids)),
    )


async def _notify_documents(session, request: Request, doc: Document) -> None:
    hub = request.app.state.hub
    if doc.team_ids is None and doc.company_id is None:
        await hub.broadcast({"type": "documents"})
        return
    stmt = select(Team.id)
    if doc.company_id is not None:
        stmt = stmt.where(Team.company_id == doc.company_id)
    if doc.team_ids is not None:
        stmt = stmt.where(Team.id.in_(doc.team_ids))
    for team_id in await session.scalars(stmt):
        await hub.publish(team_id, {"type": "documents"})


async def _doc_admin_out(session, d: Document) -> dict:
    codes = None
    if d.team_ids:
        codes = sorted(await session.scalars(select(Team.code).where(Team.id.in_(d.team_ids))))
    phase = await session.get(Phase, d.phase_id) if d.phase_id else None
    return {
        "id": d.id,
        "title": d.title,
        "kind": d.kind,
        "filename": d.filename,
        "size_bytes": d.size_bytes,
        "round": phase.key if phase else None,
        "company": d.company_id,
        "teams": codes,
        "released_at": d.released_at,
        "visible": d.released_at is not None or bool(phase and phase.started_at),
        "created_at": d.created_at,
    }


@admin.post("/documents", status_code=201)
async def add_document(body: DocumentIn, session: DbSession, request: Request) -> dict:
    if (body.body is None) == (body.file is None):
        raise ApiError(422, "BAD_DOCUMENT", "send either body (markdown) or file, not both")
    event = await current_event(session)
    doc = Document(event_id=event.id, title=body.title.strip())
    if body.file is not None:
        try:
            data = base64.b64decode(body.file.data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError(422, "BAD_DOCUMENT", "file.data_base64 isn't valid base64") from exc
        if len(data) > MAX_FILE_BYTES:
            raise ApiError(413, "DOCUMENT_TOO_LARGE", "files are limited to 15 MB")
        doc.kind, doc.data, doc.size_bytes = "file", data, len(data)
        doc.filename = re.sub(r'[\\/"\r\n]', "_", body.file.filename)
        doc.content_type = body.file.content_type
    else:
        doc.kind, doc.body, doc.size_bytes = "markdown", body.body, len(body.body.encode())
    if body.round is not None:
        doc.phase_id = (await _round(session, body.round)).id
    if body.company is not None:
        if await session.get(Company, body.company) is None:
            raise ApiError(404, "NOT_FOUND", f"no company {body.company!r}")
        doc.company_id = body.company
    if body.teams is not None:
        found = dict(
            (
                await session.execute(select(Team.code, Team.id).where(Team.code.in_(body.teams)))
            ).all()
        )
        missing = sorted(set(body.teams) - set(found))
        if missing:
            raise ApiError(404, "NOT_FOUND", f"no team(s) {missing}")
        doc.team_ids = list(found.values())
    if body.release_now:
        doc.released_at = datetime.now(UTC)
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    out = await _doc_admin_out(session, doc)
    if out["visible"]:
        await _notify_documents(session, request, doc)
    return out


@admin.get("/documents")
async def list_documents_admin(session: DbSession) -> list[dict]:
    event = await current_event(session)
    docs = await session.scalars(
        select(Document).where(Document.event_id == event.id).order_by(Document.created_at)
    )
    return [await _doc_admin_out(session, d) for d in docs.all()]


@admin.patch("/documents/{doc_id}")
async def edit_document(
    doc_id: uuid.UUID, body: DocumentPatch, session: DbSession, request: Request
) -> dict:
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise ApiError(404, "NOT_FOUND", "no such document")
    if body.title is not None:
        doc.title = body.title.strip()
    if body.released is True and doc.released_at is None:
        doc.released_at = datetime.now(UTC)
    elif body.released is False:
        doc.released_at = None  # note: a round that has started still shows it
    await session.commit()
    await _notify_documents(session, request, doc)
    return await _doc_admin_out(session, doc)


@admin.delete("/documents/{doc_id}")
async def delete_document(doc_id: uuid.UUID, session: DbSession, request: Request) -> dict:
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise ApiError(404, "NOT_FOUND", "no such document")
    await session.delete(doc)
    await session.commit()
    await _notify_documents(session, request, doc)
    return {"deleted": str(doc_id)}


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: Literal["markdown", "file"]
    filename: str | None
    content_type: str | None
    size_bytes: int
    round: str | None
    created_at: datetime


@teams.get("/documents", response_model=list[DocumentOut])
async def documents(team: CurrentTeam, session: DbSession) -> list[DocumentOut]:
    event = await current_event(session)
    rows = await session.execute(
        select(Document, Phase.key)
        .outerjoin(Phase, Phase.id == Document.phase_id)
        .where(Document.event_id == event.id, _visible_to(team))
        .order_by(Document.created_at.desc())
    )
    return [
        DocumentOut(
            id=d.id,
            title=d.title,
            kind=d.kind,
            filename=d.filename,
            content_type=d.content_type,
            size_bytes=d.size_bytes,
            round=key,
            created_at=d.created_at,
        )
        for d, key in rows.all()
    ]


@teams.get("/documents/{doc_id}")
async def document(doc_id: uuid.UUID, team: CurrentTeam, session: DbSession):
    doc = await session.scalar(
        select(Document)
        .options(undefer(Document.data))
        .outerjoin(Phase, Phase.id == Document.phase_id)
        .where(Document.id == doc_id, _visible_to(team))
    )
    if doc is None:  # not released, or not for this team: the same answer as "no such"
        raise ApiError(404, "NOT_FOUND", "no such document")
    if doc.kind == "markdown":
        return {"id": doc.id, "title": doc.title, "body": doc.body}
    return Response(
        content=doc.data,
        media_type=doc.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{doc.filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


# ------------------------------------------------------------------ round status
class StatusEntry(BaseModel):
    team: str
    status: Literal["advancing", "eliminated", "waitlisted", "pending"]
    message: str = Field(default="", max_length=2000)


class StatusesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[StatusEntry] = Field(min_length=1, max_length=500)
    publish: bool = False  # false: drafts, released later with /publish


async def _notify_status(request: Request, team_ids) -> None:
    for team_id in team_ids:
        await request.app.state.hub.publish(team_id, {"type": "rounds"})


@admin.get("/phases/{key}/statuses")
async def round_statuses(key: str, session: DbSession) -> list[dict]:
    p = await _round(session, key)
    rows = await session.execute(
        select(Team.code, Team.name, Team.company_id, TeamRoundStatus)
        .outerjoin(
            TeamRoundStatus,
            and_(TeamRoundStatus.team_id == Team.id, TeamRoundStatus.phase_id == p.id),
        )
        .order_by(Team.code)
    )
    return [
        {
            "team": code,
            "name": name,
            "company": company,
            "status": s.status if s else None,
            "message": s.message if s else "",
            "published": bool(s and s.published_at),
        }
        for code, name, company, s in rows.all()
    ]


@admin.put("/phases/{key}/statuses")
async def set_round_statuses(
    key: str, body: StatusesIn, session: DbSession, request: Request
) -> dict:
    p = await _round(session, key)
    codes = [e.team for e in body.entries]
    ids = dict(
        (await session.execute(select(Team.code, Team.id).where(Team.code.in_(codes)))).all()
    )
    missing = sorted(set(codes) - set(ids))
    if missing:
        raise ApiError(404, "NOT_FOUND", f"no team(s) {missing}")
    now = datetime.now(UTC)
    notify = []
    for e in body.entries:
        values = {"status": e.status, "message": e.message.strip(), "updated_at": now}
        if body.publish:
            values["published_at"] = now
        stmt = pg_insert(TeamRoundStatus).values(team_id=ids[e.team], phase_id=p.id, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TeamRoundStatus.team_id, TeamRoundStatus.phase_id], set_=values
        ).returning(TeamRoundStatus.published_at)
        if (await session.execute(stmt)).scalar_one() is not None:
            notify.append(ids[e.team])  # published (now, or earlier and just edited)
    await session.commit()
    await _notify_status(request, notify)
    return {"round": key, "saved": len(body.entries), "published": len(notify)}


@admin.post("/phases/{key}/statuses/publish")
async def publish_round_statuses(key: str, session: DbSession, request: Request) -> dict:
    p = await _round(session, key)
    team_ids = (
        await session.scalars(
            update(TeamRoundStatus)
            .where(TeamRoundStatus.phase_id == p.id, TeamRoundStatus.published_at.is_(None))
            .values(published_at=func.now())
            .returning(TeamRoundStatus.team_id)
        )
    ).all()
    await session.commit()
    await _notify_status(request, team_ids)
    return {"round": key, "published": len(team_ids)}


class MyStatusOut(BaseModel):
    status: Literal["advancing", "eliminated", "waitlisted", "pending"]
    message: str
    published_at: datetime


class RoundOut(BaseModel):
    key: str
    name: str
    seq: int
    status: str  # scheduled | running | paused | ended
    planned_seconds: int
    capabilities: dict
    my_status: MyStatusOut | None  # only once organisers have published it


@teams.get("/rounds", response_model=list[RoundOut])
async def rounds(team: CurrentTeam, session: DbSession) -> list[RoundOut]:
    event = await current_event(session)
    rows = await session.execute(
        select(Phase, TeamRoundStatus)
        .outerjoin(
            TeamRoundStatus,
            and_(
                TeamRoundStatus.phase_id == Phase.id,
                TeamRoundStatus.team_id == team.team_id,
                TeamRoundStatus.published_at.is_not(None),
            ),
        )
        .where(Phase.event_id == event.id)
        .order_by(Phase.seq)
    )
    return [
        RoundOut(
            key=p.key,
            name=p.display_name,
            seq=p.seq,
            status=p.status,
            planned_seconds=p.planned_seconds,
            capabilities=p.capabilities,
            my_status=(
                MyStatusOut(status=s.status, message=s.message, published_at=s.published_at)
                if s
                else None
            ),
        )
        for p, s in rows.all()
    ]
