"""Team self-registration with thapar.edu addresses; the access code goes out by email.

The code is never shown on screen or returned by the API: only someone who can read a
member's inbox gets it. That is the whole proof that the addresses are real.

Public:
  GET  /registration       open or closed, how many members, which domain
  POST /registration       {"team_name", "members": [{"name", "email"}, ...]}
Organisers (bearer ADMIN_TOKEN):
  GET  /admin/registrations                  teams, members, last email status, SMTP ok?
  POST /admin/teams/{code}/resend            new code, laptops logged out, emailed again
  DELETE /admin/teams/{code}                 remove a team that hasn't played (a mistaken sign-up)
  PATCH /admin/config {"registration": {...}} open/close, member limits, domain
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app import mail
from app.api.admin import require_admin
from app.api.errors import ApiError
from app.auth.codes import generate_access_code, hash_access_code
from app.auth.context import DbSession
from app.config import get_settings
from app.content.store import current_event
from app.models import (
    EmailLog,
    Message,
    Team,
    TeamDepartmentState,
    TeamMember,
    TeamSession,
    TeamStakeholderState,
)

public = APIRouter(tags=["registration"])
admin = APIRouter(prefix="/admin", tags=["organisers"], dependencies=[Depends(require_admin)])

DEFAULT_REGISTRATION = {"open": False, "min_members": 2, "max_members": 4, "domain": "thapar.edu"}
EMAIL = re.compile(r"^[a-z0-9._%+-]+@([a-z0-9-]+\.)+[a-z]{2,}$")

# a brake on scripted sign-ups. Generous on purpose: a whole campus can share one public IP
_recent: dict[str, deque] = defaultdict(deque)
RATE_LIMIT, RATE_WINDOW = 60, 600  # registrations per IP per 10 minutes


def rules(config: dict) -> dict:
    return {**DEFAULT_REGISTRATION, **config.get("registration", {})}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _base_url(request: Request) -> str:
    return (get_settings().public_url or str(request.base_url)).rstrip("/") + "/"


class RulesOut(BaseModel):
    open: bool
    min_members: int
    max_members: int
    domain: str


@public.get("/registration", response_model=RulesOut)
async def registration_rules(session: DbSession) -> RulesOut:
    return RulesOut(**rules((await current_event(session)).config))


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=200)


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_name: str = Field(min_length=2, max_length=60)
    members: list[MemberIn] = Field(min_length=1, max_length=10)
    website: str = ""  # honeypot: a real person never fills this hidden field


class RegisteredOut(BaseModel):
    team_code: str
    team_name: str
    emailed_to: list[str]
    email_status: str  # sent | failed | not_configured (organisers will resend)


async def _next_code(session, prefix: str = "T") -> str:
    taken = set(await session.scalars(select(Team.code).where(Team.code.like(f"{prefix}%"))))
    n = 1
    while f"{prefix}{n:03d}" in taken:
        n += 1
    return f"{prefix}{n:03d}"


async def _mail_code(session, request: Request, team: Team, access_code: str) -> tuple[str, list]:
    members = (
        await session.scalars(
            select(TeamMember.email)
            .where(TeamMember.team_id == team.id)
            .order_by(TeamMember.created_at)
        )
    ).all()
    subject, text = mail.access_code_email(team.name, team.code, access_code, _base_url(request))
    status, error = await mail.send(list(members), subject, text)
    session.add(EmailLog(team_id=team.id, recipients=list(members), subject=subject,
                         status=status, error=error))  # fmt: skip
    await session.commit()
    return status, list(members)


@public.post("/registration", response_model=RegisteredOut, status_code=201)
async def register(body: RegisterIn, session: DbSession, request: Request) -> RegisteredOut:
    event = await current_event(session)
    r = rules(event.config)
    if not r["open"]:
        raise ApiError(409, "REGISTRATION_CLOSED", "registration is closed")
    if body.website:  # a bot filled the honeypot: pretend it worked, do nothing
        raise ApiError(409, "REGISTRATION_CLOSED", "registration is closed")
    ip = _client_ip(request)
    now = time.monotonic()
    q = _recent[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise ApiError(429, "TOO_MANY_REGISTRATIONS", "too many registrations from here; try later")

    if not r["min_members"] <= len(body.members) <= r["max_members"]:
        raise ApiError(422, "BAD_TEAM_SIZE",
                       f"a team has {r['min_members']} to {r['max_members']} members")  # fmt: skip
    domain = r["domain"].lower().lstrip("@")
    emails = [m.email.strip().lower() for m in body.members]
    bad = [e for e in emails if not EMAIL.match(e) or not e.endswith("@" + domain)]
    if bad:
        raise ApiError(422, "BAD_EMAIL", f"every member needs a @{domain} address",
                       {"emails": bad})  # fmt: skip
    if len(set(emails)) != len(emails):
        raise ApiError(422, "BAD_EMAIL", "the same address is listed twice")
    taken = sorted(
        await session.scalars(select(TeamMember.email).where(TeamMember.email.in_(emails)))
    )
    if taken:
        raise ApiError(409, "EMAIL_TAKEN", "already registered with another team",
                       {"emails": taken})  # fmt: skip
    name = " ".join(body.team_name.split())
    if await session.scalar(select(func.count()).where(func.lower(Team.name) == name.lower())):
        raise ApiError(409, "NAME_TAKEN", "another team already has that name")

    access_code = generate_access_code()
    team = Team(
        code=await _next_code(session),
        name=name,
        company_id=None,
        access_code_hash=hash_access_code(access_code),
        max_sessions=event.config.get("max_sessions_per_team", 2),
    )
    session.add(team)
    await session.flush()
    session.add_all(
        [
            TeamMember(team_id=team.id, name=m.name.strip(), email=e)
            for m, e in zip(body.members, emails, strict=True)
        ]
    )
    try:
        await session.commit()
    except IntegrityError as exc:  # two sign-ups with the same address at the same moment
        await session.rollback()
        raise ApiError(409, "EMAIL_TAKEN", "one of these addresses was just registered") from exc
    q.append(now)
    status, to = await _mail_code(session, request, team, access_code)
    return RegisteredOut(
        team_code=team.code, team_name=team.name, emailed_to=to, email_status=status
    )


# ------------------------------------------------------------------ organisers
@admin.get("/registrations")
async def registrations(session: DbSession) -> dict:
    teams = (await session.scalars(select(Team).order_by(Team.code))).all()
    members: dict = {}
    for m in (await session.scalars(select(TeamMember).order_by(TeamMember.created_at))).all():
        members.setdefault(m.team_id, []).append({"name": m.name, "email": m.email})
    last: dict = {}
    for e in (await session.scalars(select(EmailLog).order_by(EmailLog.created_at))).all():
        last[e.team_id] = {"status": e.status, "error": e.error, "at": e.created_at}
    return {
        "rules": rules((await current_event(session)).config),
        "smtp_configured": mail.configured(),
        "teams": [
            {
                "code": t.code,
                "name": t.name,
                "company": t.company_id,
                "members": members.get(t.id, []),
                "last_email": last.get(t.id),
            }  # fmt: skip
            for t in teams
        ],
    }


@admin.post("/teams/{code}/resend")
async def resend(code: str, session: DbSession, request: Request) -> dict:
    """A new code (the old one stops working, its laptops are logged out), emailed again."""
    team = await session.scalar(select(Team).where(Team.code == code))
    if team is None:
        raise ApiError(404, "NOT_FOUND", f"no team {code!r}")
    if not await session.scalar(select(func.count()).where(TeamMember.team_id == team.id)):
        raise ApiError(409, "NO_MEMBERS", "this team has no registered emails; use New access code")
    access_code = generate_access_code()
    team.access_code_hash = hash_access_code(access_code)
    revoked = (
        await session.scalars(
            update(TeamSession)
            .where(TeamSession.team_id == team.id, TeamSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(TeamSession.id)
        )
    ).all()
    await session.commit()
    for sid in revoked:
        await request.app.state.hub.close_session(sid)
    status, to = await _mail_code(session, request, team, access_code)
    return {"code": code, "emailed_to": to, "email_status": status}


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
