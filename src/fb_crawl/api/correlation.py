from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from uuid import UUID, uuid4

from fastapi import Request, Response


_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="")


def current_request_id() -> str:
    return _REQUEST_ID.get()


def safe_request_id(value: str | None) -> str:
    try:
        return str(UUID(value)) if value is not None else str(uuid4())
    except (AttributeError, TypeError, ValueError):
        return str(uuid4())


async def correlate_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = safe_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    token = _REQUEST_ID.set(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        _REQUEST_ID.reset(token)
