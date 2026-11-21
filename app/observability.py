"""JSON operational events and request correlation."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("perplexed.events")
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_windows: dict[str, tuple[int, int]] = {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                    timespec="milliseconds"
                ),
                "level": record.levelname,
                "service": "perplexed-backend",
                "event": record.msg,
                **getattr(record, "fields", {}),
            },
            separators=(",", ":"),
        )


def configure_logging(level: str) -> None:
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.propagate = False


def event(name: str, *, level: int = logging.INFO, limit: int | None = None, **fields) -> None:
    """Use fixed event names and explicitly selected, JSON-safe fields."""
    if not logger.isEnabledFor(level):
        return
    if limit is not None:
        minute = int(time.monotonic() // 60)
        previous, count = _windows.get(name, (minute, 0))
        if previous != minute:
            if count > limit:
                logger.log(
                    level,
                    "logs.suppressed",
                    extra={"fields": {"category": name, "count": count - limit}},
                )
            count = 0
        _windows[name] = (minute, count + 1)
        if count >= limit:
            return
    if correlation := request_id.get():
        fields["request_id"] = correlation
    logger.log(level, name, extra={"fields": fields})


def exception_fields(exc: Exception) -> dict:
    # Messages and source lines can contain SQL parameters, provider output or credentials.
    return {
        "exception_type": type(exc).__name__,
        "stack": [
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__, limit=-12)
        ],
    }


class RequestLogging:
    def __init__(self, app: ASGIApp, slow_ms: int) -> None:
        self.app = app
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"] if scope["type"] == "http" else ""
        if not (path.startswith("/api/") or path == "/healthz"):
            await self.app(scope, receive, send)
            return

        correlation = uuid.uuid4().hex
        token = request_id.set(correlation)
        state = scope.setdefault("state", {})
        started = time.perf_counter()
        status = None
        failure = {}
        cancelled = False

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = correlation
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            failure = exception_fields(exc)
            if status is not None:
                # Abort the response without forwarding sensitive exception text to Uvicorn.
                raise RuntimeError("API response interrupted") from None
            state["error_code"] = "INTERNAL_ERROR"
            response = JSONResponse(
                {"error": {"code": "INTERNAL_ERROR", "message": "internal server error",
                           "details": {}}},
                status_code=500,
            )  # fmt: skip
            await response(scope, receive, send_with_id)
        finally:
            try:
                route = getattr(scope.get("route"), "path", None) or (
                    "/healthz" if path == "/healthz" else "unmatched"
                )
                # Normal API routes have a registered template. Never fall back to the raw URL.
                method = scope["method"]
                if method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
                    method = "OTHER"
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                fields = {
                    "method": method,
                    "route": route,
                    "status": status,
                    "duration_ms": elapsed,
                    **failure,
                }
                for key in ("actor", "team_id", "target_team_id", "error_code"):
                    if key in state:
                        fields[key] = state[key]
                if failure or (status is not None and status >= 500):
                    event("http.failed", level=logging.ERROR, limit=60, **fields)
                elif cancelled or status is None:
                    event("http.interrupted", level=logging.WARNING, limit=30, **fields)
                elif status >= 400:
                    category = "auth.rejected" if status in {401, 403} else "http.rejected"
                    event(category, level=logging.WARNING, limit=60, **fields)
                elif 200 <= status < 300 and method in {"POST", "PUT", "PATCH", "DELETE"}:
                    event(
                        "admin.write" if state.get("actor") == "admin" else "http.write", **fields
                    )
                elif elapsed >= self.slow_ms:
                    event("http.slow_read", level=logging.WARNING, limit=30, **fields)
                else:
                    event("http.read", level=logging.DEBUG, limit=120, **fields)
            finally:
                request_id.reset(token)
