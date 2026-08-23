from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .orchestrator import LiveChatUnavailable
from .result_provider import (
    ResultAuthenticationFailed,
    ResultProviderUnavailable,
    ResultSessionExpired,
)


def _json_error(status_code: int, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": str(error)})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LiveChatUnavailable)
    async def live_chat_unavailable(_: Request, error: LiveChatUnavailable) -> JSONResponse:
        return _json_error(503, error)

    @app.exception_handler(ResultProviderUnavailable)
    async def result_provider_unavailable(_: Request, error: ResultProviderUnavailable) -> JSONResponse:
        return _json_error(503, error)

    @app.exception_handler(ResultAuthenticationFailed)
    async def result_authentication_failed(_: Request, error: ResultAuthenticationFailed) -> JSONResponse:
        return _json_error(401, error)

    @app.exception_handler(ResultSessionExpired)
    async def result_session_expired(_: Request, error: ResultSessionExpired) -> JSONResponse:
        return _json_error(401, error)
