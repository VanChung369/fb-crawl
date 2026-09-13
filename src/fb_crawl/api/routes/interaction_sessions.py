from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PayloadError
from starlette.concurrency import run_in_threadpool

from fb_crawl.api.dependencies import CurrentAccount, ProductAuthenticationError
from fb_crawl.interaction_sessions.models import (
    RowFilters, SessionCreate, SessionError, SessionFilters, SessionIdentity, SessionRowInput,
)


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePayload(StrictPayload):
    client_session_id: UUID
    source_url: str = Field(max_length=2048)
    kind: Literal["comments", "reactions", "friends"]


class IdentityPayload(StrictPayload):
    facebook_uid: str = Field(default="", max_length=20)
    username: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=512)
    profile_url: str = Field(default="", max_length=2048)


class RowPayload(StrictPayload):
    client_row_id: UUID
    row_revision: int = Field(strict=True, ge=1, le=2147483647)
    interaction_id: str = Field(min_length=1, max_length=512)
    synthetic: bool = Field(strict=True)
    parent_id: str = Field(default="", max_length=512)
    kind: Literal["comment", "reply", "reaction", "friend"]
    identity: IdentityPayload
    text: str = Field(max_length=10000)
    observed_at: datetime

    def to_domain(self):
        return SessionRowInput(**{**self.model_dump(), "identity": SessionIdentity(**self.identity.model_dump())})


class BatchPayload(StrictPayload):
    rows: list[RowPayload] = Field(min_length=1, max_length=100)


class TransitionPayload(StrictPayload):
    status: Literal["running", "stopped"]
    revision: int = Field(strict=True, ge=1)


class LookupPayload(StrictPayload):
    retry_failed: bool = Field(default=False, strict=True)
    resolved_uid: str | None = Field(default=None, strict=True, min_length=5, max_length=20, pattern=r"^[1-9][0-9]*$")


def create_interaction_sessions_router(service, current_account_dependency, *, lookup_service=None, rate_limiter=None, clock=lambda: datetime.now(UTC)):
    router = APIRouter(prefix="/api/v1/interaction-sessions", tags=["interaction-sessions"])

    async def account(current: CurrentAccount = Depends(current_account_dependency)):
        if current.cookie_authenticated:
            raise ProductAuthenticationError()
        if not current.device_allowed:
            raise SessionError("contact_device_not_allowed", 403)
        return current

    @router.post("")
    def create(payload: CreatePayload, current=Depends(account)):
        return service.create(current.account.id, SessionCreate(**payload.model_dump()), clock())

    @router.get("")
    def sessions(source_url: str | None = Query(None, max_length=2048), status: str | None = None,
                 created_from: datetime | None = None, created_to: datetime | None = None,
                 cursor: str | None = Query(None, max_length=1024), limit: int = Query(100, ge=1, le=100), current=Depends(account)):
        return service.list_sessions(current.account.id, SessionFilters(source_url, created_from, created_to, status), cursor, limit)

    @router.get("/{session_id}")
    def get(session_id: UUID, current=Depends(account)):
        return service.get(current.account.id, session_id)

    @router.put("/{session_id}/interactions")
    async def upload(session_id: UUID, request: Request, current=Depends(account)):
        size, parts = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1024 * 1024:
                raise SessionError("session_payload_too_large", 413)
            parts.append(chunk)
        try:
            payload = BatchPayload.model_validate_json(b"".join(parts))
        except PayloadError as error:
            raise RequestValidationError(error.errors()) from error
        rows = tuple(row.to_domain() for row in payload.rows)
        return await run_in_threadpool(service.upsert_rows, current.account.id, session_id, rows, clock())

    @router.patch("/{session_id}")
    def transition(session_id: UUID, payload: TransitionPayload, current=Depends(account)):
        return service.transition(current.account.id, session_id, payload.status, payload.revision, clock())

    @router.delete("/{session_id}", status_code=204)
    def delete(session_id: UUID, current=Depends(account)):
        service.delete(current.account.id, session_id)

    @router.get("/{session_id}/interactions")
    def rows(session_id: UUID, author: str | None = Query(None, max_length=512), text: str | None = Query(None, max_length=512),
             kind: str | None = None, outcome: str | None = None, cursor: str | None = Query(None, max_length=1024),
             limit: int = Query(100, ge=1, le=100), current=Depends(account)):
        return service.list_rows(current.account.id, session_id, RowFilters(author, text, kind, outcome), cursor, limit)

    @router.post("/{session_id}/people/{person_id}/lookup")
    def lookup(session_id: UUID, person_id: UUID, payload: LookupPayload, request: Request, current=Depends(account)):
        from fb_crawl.api.routes.contacts import _lookup_response
        if lookup_service is None:
            raise SessionError("provider_not_configured", 503)
        if rate_limiter is not None:
            rate_limiter.check("contact_lookup", str(current.account.id), str(current.device.id),
                               request.client.host if request.client else "unknown", clock())
        result = lookup_service.lookup(current.account, current.device, session_id, person_id, clock(), payload.retry_failed, resolved_uid=payload.resolved_uid)
        return _lookup_response(result)

    return router
