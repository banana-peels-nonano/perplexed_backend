"""The FastAPI application.

Run locally:   uvicorn app.main:app --reload
Then open:     http://127.0.0.1:8000/docs   (interactive API page, generated from the code)
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from app.api import auth, stakeholders
from app.api.errors import install_error_handlers
from app.auth.context import DbSession

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(title="PERPLEXED 2.0 backend", version="0.1.0")
    install_error_handlers(app)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(stakeholders.router, prefix=API_PREFIX)

    @app.get("/healthz", tags=["ops"])
    async def healthz(session: DbSession) -> dict:
        await session.execute(text("SELECT 1"))  # proves the database is reachable too
        return {"status": "ok"}

    return app


app = create_app()
