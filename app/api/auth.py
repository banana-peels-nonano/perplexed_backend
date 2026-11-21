"""POST /auth/login, POST /auth/logout, GET /me."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.auth.context import CurrentTeam, DbSession
from app.auth.sessions import COOKIE_NAME
from app.auth.sessions import login as do_login
from app.auth.sessions import logout as do_logout
from app.config import get_settings

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    access_code: str = Field(min_length=4, max_length=64)
    device_label: str | None = Field(default=None, max_length=64)


class TeamOut(BaseModel):
    code: str
    name: str | None = None
    company_id: str | None


@router.post("/auth/login", response_model=TeamOut)
async def login(
    body: LoginRequest, response: Response, session: DbSession, request: Request
) -> TeamOut:
    settings = get_settings()
    team, token = await do_login(
        session, body.access_code, body.device_label, settings.session_ttl_hours
    )
    request.state.actor = "team"
    request.state.team_id = str(team.id)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,  # page JavaScript can't read it, so injected scripts can't steal it
        samesite="strict",  # not sent on requests started by other sites
        secure=settings.cookie_secure,  # HTTPS-only in production
        path="/",
    )
    return TeamOut(code=team.code, name=team.name, company_id=team.company_id)


@router.post("/auth/logout", status_code=204)
async def logout(team: CurrentTeam, session: DbSession, request: Request) -> Response:
    await do_logout(session, team.session_id)
    await request.app.state.hub.close_session(team.session_id)  # this laptop's live tabs
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=TeamOut)
async def me(team: CurrentTeam) -> TeamOut:
    return TeamOut(code=team.team_code, company_id=team.company_id)
