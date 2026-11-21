"""GET /api/v1/ws: the live-notification socket (app/live/hub.py says what it carries).

The handshake uses the same session cookie as every other request. Close codes a client
should handle:
    4401  not logged in / logged out: go to the login screen
    4403  wrong origin (another site tried to open it)
    4429  too many open tabs for this team: close some
Anything else: reconnect after a few seconds, then refetch what's on screen.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.interview import Maker
from app.auth.context import team_from_token
from app.auth.sessions import COOKIE_NAME
from app.live.hub import Connection, Hub
from app.observability import event, exception_fields, request_id

router = APIRouter(tags=["live"])
PING_SECONDS = 25.0


def _same_origin(websocket: WebSocket) -> bool:
    """Browsers always send Origin on a WebSocket handshake; it must be our own host.
    (The SameSite=Strict cookie already stops other sites; this is a second lock.)"""
    origin = websocket.headers.get("origin")
    if origin is None:
        return True  # not a browser (scripts, tests): the cookie still has to be valid
    return urlparse(origin).netloc == websocket.headers.get("host")


async def serve(
    websocket: WebSocket,
    maker: async_sessionmaker[AsyncSession],
    hub: Hub,
    ping_seconds: float = PING_SECONDS,
) -> None:
    await websocket.accept()
    if not _same_origin(websocket):
        event("ws.rejected", level=logging.WARNING, limit=60, close_code=4403)
        await websocket.close(code=4403, reason="wrong origin")
        return
    token = websocket.cookies.get(COOKIE_NAME)
    team = None
    if token:
        async with maker() as session:
            team = await team_from_token(session, token)
    if team is None:
        event("ws.rejected", level=logging.WARNING, limit=60, close_code=4401)
        await websocket.close(code=4401, reason="log in first")
        return
    conn = Connection(websocket, team.team_id, team.session_id)
    if not hub.add(conn):
        event(
            "ws.rejected", level=logging.WARNING, limit=60,
            close_code=4429, team_id=str(team.team_id),
        )  # fmt: skip
        await websocket.close(code=4429, reason="too many open tabs for this team")
        return
    started = time.perf_counter()
    close_code = None
    event("ws.connected", limit=120, team_id=str(team.team_id), connections=hub.count())
    try:
        await websocket.send_json({"type": "hello", "team": team.team_code})
        while True:
            try:  # nothing useful comes from the client; we only watch for it leaving
                await asyncio.wait_for(websocket.receive_text(), ping_seconds)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect as exc:
        close_code = exc.code
    except RuntimeError:
        pass
    finally:
        hub.remove(conn)
        event(
            "ws.disconnected",
            level=logging.DEBUG,
            limit=120,
            team_id=str(team.team_id),
            close_code=close_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )


@router.websocket("/ws")
async def ws(websocket: WebSocket, maker: Maker) -> None:
    token = request_id.set(uuid.uuid4().hex)
    try:
        await serve(websocket, maker, websocket.app.state.hub)
    except Exception as exc:
        event("ws.failed", level=logging.ERROR, limit=60, **exception_fields(exc))
        try:
            await websocket.close(code=1011)
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
    finally:
        request_id.reset(token)
