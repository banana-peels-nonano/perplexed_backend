"""The FastAPI application.

Run locally:   uvicorn app.main:app --reload
Then open:     http://127.0.0.1:8000/docs   (interactive API page, generated from the code)
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from app.api import auth, interview, portal, stakeholders
from app.api.errors import install_error_handlers
from app.auth.context import DbSession
from app.config import get_settings
from app.content.store import ContentStore
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
    install_error_handlers(app)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(stakeholders.router, prefix=API_PREFIX)
    app.include_router(interview.router, prefix=API_PREFIX)
    app.include_router(portal.router, prefix=API_PREFIX)

    @app.get("/healthz", tags=["ops"])
    async def healthz(session: DbSession) -> dict:
        await session.execute(text("SELECT 1"))  # proves the database is reachable too
        return {"status": "ok"}

    return app


app = create_app()
