"""Scores, the leaderboard, and a read-only look at a team's evidence board.

  PUT /admin/phases/{key}/scores    set points per team for a round (live: no publish step)
  GET /admin/leaderboard            totals plus per-round points and notes
  GET /admin/teams/{code}/board     the team's board, evidence cards filled in with the passage
  PUT /admin/phases/{key}/scores/visible   show or hide a round's scores to teams
  GET /leaderboard                  what teams see: only rounds organisers have shown

Teams that have no score yet are listed with 0, so the board shows everyone from the start.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm.attributes import flag_modified

from app.api.admin import require_admin
from app.api.errors import ApiError
from app.auth.context import CurrentTeam, DbSession
from app.content.store import current_event
from app.models import Passage, Phase, Record, Team, TeamBoard, TeamScore

admin = APIRouter(prefix="/admin", tags=["organisers"], dependencies=[Depends(require_admin)])
teams = APIRouter(tags=["rounds"])


class ScoreEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str
    points: Decimal | None = Field(default=None, ge=-1000, le=1000)  # None: clear the score
    note: str = Field(default="", max_length=4000)  # feedback, organisers only
    breakdown: dict[str, float] = Field(default_factory=dict, max_length=12)  # criterion: points


class ScoresIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[ScoreEntry] = Field(min_length=1, max_length=500)


async def _leaderboard(session) -> dict:
    event = await current_event(session)
    phases = (
        await session.execute(
            select(Phase.id, Phase.key, Phase.display_name)
            .where(Phase.event_id == event.id)
            .order_by(Phase.seq)
        )
    ).all()
    scores: dict = {}
    for team_id, phase_id, points, note, breakdown in (
        await session.execute(
            select(
                TeamScore.team_id, TeamScore.phase_id, TeamScore.points, TeamScore.note,
                TeamScore.breakdown,
            )
        )
    ).all():
        scores.setdefault(team_id, {})[phase_id] = (points, note, breakdown)
    key = {pid: k for pid, k, _ in phases}
    rows = []
    for t in (await session.scalars(select(Team).order_by(Team.code))).all():
        mine = scores.get(t.id, {})
        rows.append(
            {
                "team": t.code,
                "name": t.name,
                "company": t.company_id,
                "total": float(sum((v[0] for v in mine.values()), Decimal(0))),
                "rounds": {key[pid]: float(v[0]) for pid, v in mine.items() if pid in key},
                "notes": {key[pid]: v[1] for pid, v in mine.items() if pid in key and v[1]},
                "breakdowns": {key[pid]: v[2] for pid, v in mine.items() if pid in key and v[2]},
            }
        )
    rows.sort(key=lambda r: (-r["total"], r["team"]))
    for i, r in enumerate(rows):  # equal totals share a rank
        r["rank"] = rows[i - 1]["rank"] if i and r["total"] == rows[i - 1]["total"] else i + 1
    scored = {k for r in rows for k in r["rounds"]}
    visible = set(event.config.get("leaderboard_visible") or [])
    return {
        "rounds": [{"key": k, "name": n, "visible": k in visible} for _, k, n in phases if k in scored],
        "teams": rows,
    }


class VisibleIn(BaseModel):
    visible: bool


@admin.put("/phases/{key}/scores/visible")
async def set_scores_visible(key: str, body: VisibleIn, session: DbSession, request: Request) -> dict:
    """Teams see a round's scores only once organisers show them; hiding takes them back."""
    event = await current_event(session)
    if await session.scalar(select(Phase.id).where(Phase.event_id == event.id, Phase.key == key)) is None:
        raise ApiError(404, "NOT_FOUND", f"no round {key!r}")
    shown = set(event.config.get("leaderboard_visible") or [])
    shown = shown | {key} if body.visible else shown - {key}
    event.config = {**event.config, "leaderboard_visible": sorted(shown)}
    flag_modified(event, "config")
    await session.commit()
    await request.app.state.hub.broadcast({"type": "leaderboard"})
    return {"round": key, "visible": body.visible}


@admin.get("/leaderboard")
async def admin_leaderboard(session: DbSession) -> dict:
    return await _leaderboard(session)


@admin.put("/phases/{key}/scores")
async def set_scores(key: str, body: ScoresIn, session: DbSession, request: Request) -> dict:
    event = await current_event(session)
    phase = await session.scalar(select(Phase).where(Phase.event_id == event.id, Phase.key == key))
    if phase is None:
        raise ApiError(404, "NOT_FOUND", f"no round {key!r}")
    codes = [e.team for e in body.entries]
    ids = dict((await session.execute(select(Team.code, Team.id).where(Team.code.in_(codes)))).all())
    missing = sorted(set(codes) - set(ids))
    if missing:
        raise ApiError(404, "NOT_FOUND", f"no team(s) {missing}")
    now = datetime.now(UTC)
    for e in body.entries:
        where = (TeamScore.team_id == ids[e.team], TeamScore.phase_id == phase.id)
        if e.points is None:
            await session.execute(delete(TeamScore).where(*where))
            continue
        values = {
            "points": e.points, "note": e.note.strip(), "breakdown": e.breakdown, "updated_at": now
        }
        await session.execute(
            pg_insert(TeamScore)
            .values(team_id=ids[e.team], phase_id=phase.id, **values)
            .on_conflict_do_update(index_elements=[TeamScore.team_id, TeamScore.phase_id], set_=values)
        )
    await session.commit()
    await request.app.state.hub.broadcast({"type": "leaderboard"})
    return {"round": key, "saved": len(body.entries)}


@admin.get("/teams/{code}/board")
async def team_board(code: str, session: DbSession) -> dict:
    team = await session.scalar(select(Team).where(Team.code == code))
    if team is None:
        raise ApiError(404, "NOT_FOUND", f"no team {code!r}")
    board = await session.get(TeamBoard, team.id)
    doc = dict(board.doc) if board else {"cards": [], "links": []}
    cards = list(doc.get("cards") or [])
    pids = {c.get("passage_id") for c in cards if c.get("kind") == "evidence"}
    passages = {
        pid: (text, rid, title)
        for pid, text, rid, title in (
            await session.execute(
                select(Passage.id, Passage.text, Passage.record_id, Record.title)
                .join(Record, Record.id == Passage.record_id)
                .where(Passage.id.in_(pids or {""}))
            )
        ).all()
    }
    for c in cards:
        if c.get("kind") == "evidence" and c.get("passage_id") in passages:
            text, rid, title = passages[c["passage_id"]]
            c.update(text=text, record_id=rid, record_title=title)
    return {
        "team": team.code,
        "name": team.name,
        "version": board.version if board else 0,
        "updated_at": board.updated_at if board else None,
        "cards": cards,
        "links": doc.get("links") or [],
    }


@teams.get("/leaderboard")
async def leaderboard(team: CurrentTeam, session: DbSession) -> dict:
    lb = await _leaderboard(session)
    shown = {r["key"] for r in lb["rounds"] if r["visible"]}
    lb["rounds"] = [{"key": r["key"], "name": r["name"]} for r in lb["rounds"] if r["visible"]]
    for r in lb["teams"]:  # only shown rounds; no notes, breakdowns or companies
        r["rounds"] = {k: v for k, v in r["rounds"].items() if k in shown}
        r["total"] = sum(r["rounds"].values())
        for k in ("notes", "breakdowns", "company"):
            r.pop(k)
    lb["teams"].sort(key=lambda r: (-r["total"], r["team"]))
    for i, r in enumerate(lb["teams"]):
        r["rank"] = lb["teams"][i - 1]["rank"] if i and r["total"] == lb["teams"][i - 1]["total"] else i + 1
    return lb
