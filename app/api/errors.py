"""One error shape for the whole API, so the frontend handles errors in one place:

    {"error": {"code": "QUOTA_EXHAUSTED", "message": "human-readable text", "details": {}}}

`code` is the stable, machine-readable part; the frontend switches on it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status, content=_body(exc.code, exc.message, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # malformed request bodies get the same shape as every other error. Only type,
        # location and message: pydantic's `ctx` can hold exception objects (not JSON), and
        # `input` would echo the whole body back (step 9: a board rule's ValueError crashed
        # this handler instead of returning 422)
        errors = [
            {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_body("INVALID_REQUEST", "request body is invalid", {"errors": errors}),
        )
