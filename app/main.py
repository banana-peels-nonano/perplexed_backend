"""The FastAPI application.

Run locally:   uvicorn app.main:app --reload
Then open:     http://127.0.0.1:8000/docs   (interactive API page, generated from the code)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import (
    admin,
    alliances,
    auth,
    interview,
    live,
    portal,
    registration,
    rounds,
    stakeholders,
    submissions,
)
from app.api.errors import install_error_handlers
from app.auth.context import DbSession
from app.config import get_settings
from app.content.store import ContentStore
from app.live.hub import Hub
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LlmProvider

API_PREFIX = "/api/v1"


def default_provider() -> LlmProvider | None:
    key = get_settings().anthropic_api_key
    return AnthropicProvider(key.get_secret_value()) if key else None


def create_app(provider: LlmProvider | None = None) -> FastAPI:
    """`provider` lets tests inject the fake; normally Claude, if a key is configured."""
    app = FastAPI(title="PERPLEXED 2.0 backend", version="0.2.0")
    app.state.provider = provider if provider is not None else default_provider()
    app.state.content = ContentStore()
    app.state.hub = Hub()  # live notifications (app/live/hub.py)
    install_error_handlers(app)

    # ponytail: one log line per API call (static files + /healthz skipped). Bounded by the
    # docker log cap (5x10MB); at 50 teams expect ~10-20MB/day. Errors/slow -> WARNING.
    reqlog = logging.getLogger("uvicorn.error")

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "-")
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            ms = (time.perf_counter() - t0) * 1000
            reqlog.exception("REQ %s %s -> EXC %.0fms ip=%s", request.method, path, ms, ip)
            raise
        ms = (time.perf_counter() - t0) * 1000
        level = logging.WARNING if response.status_code >= 400 or ms > 1000 else logging.INFO
        reqlog.log(level, "REQ %s %s -> %d %.0fms ip=%s", request.method, path, response.status_code, ms, ip)
        return response
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(stakeholders.router, prefix=API_PREFIX)
    app.include_router(interview.router, prefix=API_PREFIX)
    app.include_router(portal.router, prefix=API_PREFIX)
    app.include_router(admin.router, prefix=API_PREFIX)
    app.include_router(rounds.admin, prefix=API_PREFIX)
    app.include_router(rounds.teams, prefix=API_PREFIX)
    app.include_router(alliances.teams, prefix=API_PREFIX)
    app.include_router(alliances.admin, prefix=API_PREFIX)
    app.include_router(submissions.teams, prefix=API_PREFIX)
    app.include_router(submissions.admin, prefix=API_PREFIX)
    app.include_router(registration.admin, prefix=API_PREFIX)
    app.include_router(live.router, prefix=API_PREFIX)

    @app.get("/healthz", tags=["ops"])
    async def healthz(session: DbSession) -> dict:
        await session.execute(text("SELECT 1"))  # proves the database is reachable too
        return {"status": "ok"}

    mount_web(app)
    return app


WEB = Path(__file__).resolve().parents[1] / "web"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    # scripts and styles only from this server; nothing inline, nothing third-party
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


def mount_web(app: FastAPI) -> None:
    """The frontend is static files served by this same app: one origin, so the session
    cookie (SameSite=Strict, HttpOnly) just works and there's one thing to deploy.
    /         the team portal          /console  the organiser console
    /display  the projector page
    everything else under / is a static file"""
    if not WEB.is_dir():
        return

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith(("/api/", "/docs", "/redoc", "/openapi.json")):
            response.headers.update(SECURITY_HEADERS)
            # no stale JS after a deploy mid-event; files are small
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get("/console", include_in_schema=False)
    async def console() -> FileResponse:
        return FileResponse(WEB / "console.html")

    @app.get("/display", include_in_schema=False)
    async def display() -> FileResponse:
        return FileResponse(WEB / "display.html")

    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


app = create_app()
