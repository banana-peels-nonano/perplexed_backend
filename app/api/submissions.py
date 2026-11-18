"""The Audit Findings Sheet: one per team per round, every save kept as a version.

Saving is allowed only while the current round accepts submissions (capability
"submissions") and the team still has time on its clock: the lock is the server's clock,
not the browser's. A version marked final is what the team submitted; judges read the last
final version (or, if a team never pressed submit, the last saved one, flagged as such).

Teams:
  GET /submission                 this round's sheet (or the last round the team saved in)
  PUT /submission                 {"base_version", "summary", "findings", "final"}
  GET /submission/versions/{n}    an earlier version
Organisers:
  GET /admin/submissions?round=   every team's status for a round
  GET /admin/submissions/{code}?round=&version=
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select

from app.api.admin import require_admin
from app.api.errors import ApiError
from app.auth.context import CurrentTeam, DbSession
from app.content.store import current_event
from app.models import AllianceMember, Passage, Phase, SubmissionVersion, Team
from app.portal.alliance import partner_team_ids, usable_passages
from app.portal.clock import not_in_round, phase_remaining, team_bonus

teams = APIRouter(tags=["submissions"])
admin = APIRouter(prefix="/admin", tags=["organisers"], dependencies=[Depends(require_admin)])


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str = Field(max_length=200)
    finding: str = Field(default="", max_length=4000)  # what is wrong, and the evidence for it
    impact: str = Field(default="", max_length=2000)
    recommendation: str = Field(default="", max_length=2000)
    sources: list[str] = Field(default_factory=list, max_length=20)  # passage ids
    cross_company: bool = False


class SheetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version: int = Field(ge=0)  # the version this edit started from (0 = none yet)
    summary: str = Field(default="", max_length=4000)
    findings: list[Finding] = Field(default_factory=list, max_length=30)
    final: bool = False  # true = submit this version


class VersionOut(BaseModel):
    version: int
    final: bool
    saved_at: datetime


class SheetOut(BaseModel):
    round: str | None
    round_name: str | None
    open: bool
    closes_in_seconds: int | None  # None: no timer
    requires_cross_company: bool  # the team is in an alliance, so one finding must span both
    version: int
    final: bool
    saved_at: datetime | None
    summary: str
    findings: list[Finding]
    versions: list[VersionOut]


async def _active_round(session, team) -> tuple[Phase | None, bool, int | None]:
    """(the round submissions go to, whether it's open for this team, seconds left)."""
    event = await current_event(session)
    phase = await session.scalar(
        select(Phase).where(Phase.event_id == event.id, Phase.status.in_(["running", "paused"]))
    )
    if phase is None or not phase.capabilities.get("submissions"):
        return None, False, None
    left = phase_remaining(phase, datetime.now(UTC), await team_bonus(session, team.team_id, phase))
    is_open = (
        phase.status == "running"
        and (left is None or left > 0)
        and not await not_in_round(session, team.team_id, phase)
    )
    return phase, is_open, left


async def _requires_cross(session, team_id) -> bool:
    config = (await current_event(session)).config
    if not config.get("submissions", {}).get("require_cross_company", True):
        return False
    return bool(await partner_team_ids(session, team_id))


async def _sheet(session, team, phase: Phase | None, is_open: bool, left) -> SheetOut:
    if phase is None:  # no round taking submissions: show the last one this team saved in
        phase = await session.scalar(
            select(Phase)
            .join(SubmissionVersion, SubmissionVersion.phase_id == Phase.id)
            .where(SubmissionVersion.team_id == team.team_id)
            .order_by(Phase.seq.desc())
            .limit(1)
        )
    versions = []
    latest = None
    if phase is not None:
        versions = (
            await session.scalars(
                select(SubmissionVersion)
                .where(
                    SubmissionVersion.team_id == team.team_id,
                    SubmissionVersion.phase_id == phase.id,
                )
                .order_by(SubmissionVersion.version)
            )
        ).all()
        latest = versions[-1] if versions else None
    doc = latest.doc if latest else {}
    return SheetOut(
        round=phase.key if phase else None,
        round_name=phase.display_name if phase else None,
        open=is_open,
        closes_in_seconds=left,
        requires_cross_company=await _requires_cross(session, team.team_id),
        version=latest.version if latest else 0,
        final=bool(latest and latest.final),
        saved_at=latest.created_at if latest else None,
        summary=doc.get("summary", ""),
        findings=doc.get("findings", []),
        versions=[
            VersionOut(version=v.version, final=v.final, saved_at=v.created_at) for v in versions
        ],  # fmt: skip
    )


@teams.get("/submission", response_model=SheetOut)
async def get_submission(team: CurrentTeam, session: DbSession) -> SheetOut:
    phase, is_open, left = await _active_round(session, team)
    return await _sheet(session, team, phase, is_open, left)


@teams.put("/submission", response_model=SheetOut)
async def save_submission(
    body: SheetIn, team: CurrentTeam, session: DbSession, request: Request
) -> SheetOut:
    phase, is_open, left = await _active_round(session, team)
    if phase is None or not is_open:
        raise ApiError(409, "SUBMISSIONS_CLOSED", "submissions are closed")
    ids = [f.id for f in body.findings]
    if len(ids) != len(set(ids)):
        raise ApiError(422, "BAD_SHEET", "two findings share an id")
    cited = {s for f in body.findings for s in f.sources}
    unknown = sorted(cited - await usable_passages(session, team.team_id))
    if unknown:
        raise ApiError(422, "UNKNOWN_EVIDENCE", "the sheet cites evidence your team (and its "
                       "alliance) hasn't been shown", {"passage_ids": unknown})  # fmt: skip
    if body.final:
        problems = []
        if not body.findings:
            problems.append("add at least one finding")
        problems += [f"finding '{f.title or f.id}' has no title" for f in body.findings
                     if not f.title.strip()]  # fmt: skip
        problems += [f"finding '{f.title or f.id}' cites no sources" for f in body.findings
                     if not f.sources]  # fmt: skip
        if await _requires_cross(session, team.team_id):
            companies = dict(
                (await session.execute(
                    select(Passage.id, Passage.company_id).where(Passage.id.in_(cited or {""}))
                )).all()
            )  # fmt: skip
            spans = any(
                f.cross_company and any(companies.get(s) != team.company_id for s in f.sources)
                for f in body.findings
            )
            if not spans:
                problems.append(
                    "mark at least one finding as cross-company and cite your partner's "
                    "evidence in it"
                )
        if problems:
            raise ApiError(422, "SHEET_INCOMPLETE", "; ".join(problems), {"problems": problems})
    latest = await session.scalar(
        select(func.max(SubmissionVersion.version)).where(
            SubmissionVersion.team_id == team.team_id, SubmissionVersion.phase_id == phase.id
        )
    )
    if (latest or 0) != body.base_version:
        raise ApiError(409, "SUBMISSION_CONFLICT", "a teammate saved a newer version: reload",
                       {"version": latest or 0})  # fmt: skip
    session.add(
        SubmissionVersion(
            team_id=team.team_id,
            phase_id=phase.id,
            version=body.base_version + 1,
            doc={"summary": body.summary, "findings": [f.model_dump() for f in body.findings]},
            final=body.final,
        )
    )
    try:
        await session.commit()
    except Exception as exc:  # the unique (team, round, version) caught a simultaneous save
        await session.rollback()
        raise ApiError(409, "SUBMISSION_CONFLICT", "a teammate saved at the same moment: reload",
                       {"version": body.base_version + 1}) from exc  # fmt: skip
    await request.app.state.hub.publish(team.team_id, {"type": "submission"})
    return await _sheet(session, team, phase, is_open, left)


@teams.get("/submission/versions/{version}", response_model=SheetOut)
async def get_version(version: int, team: CurrentTeam, session: DbSession) -> SheetOut:
    sheet = await get_submission(team, session)
    row = await session.scalar(
        select(SubmissionVersion)
        .join(Phase, Phase.id == SubmissionVersion.phase_id)
        .where(
            SubmissionVersion.team_id == team.team_id,
            Phase.key == sheet.round,
            SubmissionVersion.version == version,
        )
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "no such version")
    return sheet.model_copy(update={
        "version": row.version, "final": row.final, "saved_at": row.created_at,
        "summary": row.doc.get("summary", ""), "findings": row.doc.get("findings", []),
    })  # fmt: skip


# ------------------------------------------------------------------ organisers
async def _round_or_current(session, key: str | None) -> Phase:
    event = await current_event(session)
    stmt = select(Phase).where(Phase.event_id == event.id)
    if key:
        phase = await session.scalar(stmt.where(Phase.key == key))
    else:  # the most recent round that took submissions
        phase = await session.scalar(
            stmt.join(SubmissionVersion, SubmissionVersion.phase_id == Phase.id)
            .order_by(Phase.seq.desc())
            .limit(1)
        )
    if phase is None:
        raise ApiError(404, "NOT_FOUND", "no such round (or no submissions yet)")
    return phase


@admin.get("/submissions")
async def submissions(session: DbSession, round: str | None = None) -> dict:
    phase = await _round_or_current(session, round)
    last = (
        select(
            SubmissionVersion.team_id,
            func.max(SubmissionVersion.version).label("version"),
            func.max(SubmissionVersion.created_at).label("saved_at"),
            func.max(SubmissionVersion.created_at)
            .filter(SubmissionVersion.final)
            .label("final_at"),
        )
        .where(SubmissionVersion.phase_id == phase.id)
        .group_by(SubmissionVersion.team_id)
        .subquery()
    )
    allied = set(await session.scalars(select(AllianceMember.team_id).where(AllianceMember.locked)))
    rows = await session.execute(
        select(Team, last.c.version, last.c.saved_at, last.c.final_at)
        .outerjoin(last, and_(last.c.team_id == Team.id))
        .order_by(Team.code)
    )
    return {
        "round": phase.key,
        "teams": [
            {
                "team": t.code,
                "name": t.name,
                "company": t.company_id,
                "allied": t.id in allied,
                "versions": v or 0,
                "last_saved_at": saved,
                "submitted_at": final_at,
            }
            for t, v, saved, final_at in rows.all()
        ],
    }


@admin.get("/submissions/{code}")
async def team_submission(
    code: str, session: DbSession, round: str | None = None, version: int | None = None
) -> dict:
    team = await session.scalar(select(Team).where(Team.code == code))
    if team is None:
        raise ApiError(404, "NOT_FOUND", f"no team {code!r}")
    phase = await _round_or_current(session, round)
    versions = (
        await session.scalars(
            select(SubmissionVersion)
            .where(SubmissionVersion.team_id == team.id, SubmissionVersion.phase_id == phase.id)
            .order_by(SubmissionVersion.version)
        )
    ).all()
    if not versions:
        return {"team": code, "round": phase.key, "sheet": None, "versions": []}
    if version is not None:
        pick = next((v for v in versions if v.version == version), None)
        if pick is None:
            raise ApiError(404, "NOT_FOUND", "no such version")
    else:  # what judges read: the last submitted version, else the last saved one
        finals = [v for v in versions if v.final]
        pick = finals[-1] if finals else versions[-1]
    return {
        "team": code,
        "name": team.name,
        "round": phase.key,
        "sheet": {
            **pick.doc,
            "version": pick.version,
            "final": pick.final,
            "saved_at": pick.created_at,
        },  # fmt: skip
        "never_submitted": not any(v.final for v in versions),
        "versions": [
            {"version": v.version, "final": v.final, "saved_at": v.created_at} for v in versions
        ],  # fmt: skip
    }
