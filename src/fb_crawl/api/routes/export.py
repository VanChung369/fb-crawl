"""Authenticated exports of all users matching the current filters."""
from __future__ import annotations

import csv
import json
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import ApiErrorResponse
from fb_crawl.api.routes.users import query_user_page, user_response_values
from fb_crawl.core.exceptions import ValidationError

ERROR_RESPONSES = {400: {"model": ApiErrorResponse}, 401: {"model": ApiErrorResponse}}
EXPORT_FIELDS = ["id", "facebook_uid", "username", "name", "profile_url", "phone_1",
                 "phone_2", "address", "birth_date", "gender", "created_at", "updated_at"]


def safe_csv_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value


def create_export_router(user_repository: Any, auth: ApiKeyAuth) -> APIRouter:
    router = APIRouter(prefix="/api/v1/export", tags=["export"], dependencies=[Depends(auth)])

    @router.get("/users", responses=ERROR_RESPONSES)
    def export_users(
        format: Literal["csv", "json"] = "csv",
        q: Annotated[str | None, Query(max_length=256)] = None,
        uid: Annotated[str | None, Query(max_length=128)] = None,
        username: Annotated[str | None, Query(max_length=256)] = None,
        phone: Annotated[str | None, Query(max_length=64)] = None,
        phone_origin: Annotated[str | None, Query(max_length=32)] = None,
        has_phone: Annotated[bool | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100, description="Rows per database page; all matching pages are exported.")] = 100,
    ) -> StreamingResponse:
        # Finish before sending headers so query failures cannot look like a
        # successful, truncated download. Large exports spill to disk.
        output = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+", encoding="utf-8", newline="")
        try:
            writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS)
            if format == "csv":
                output.write("\ufeff")
                writer.writeheader()
            else:
                output.write("[")
            cursor = None
            seen_cursors = set()
            first = True
            with user_repository.export_snapshot() as snapshot:
                while True:
                    page = query_user_page(snapshot, q=q, uid=uid, username=username,
                                           phone=phone, phone_origin=phone_origin, has_phone=has_phone,
                                           limit=limit, cursor=cursor)
                    for user in page.items:
                        row = user_response_values(user)
                        if format == "csv":
                            writer.writerow({key: safe_csv_cell(value) for key, value in row.items()})
                        else:
                            if not first:
                                output.write(",")
                            output.write(json.dumps(row, ensure_ascii=False, default=str))
                        first = False
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                    if cursor in seen_cursors:
                        raise ValidationError("Export pagination did not advance.")
                    seen_cursors.add(cursor)
            if format == "json":
                output.write("]")
            output.seek(0)
        except BaseException:
            output.close()
            raise

        def chunks():
            try:
                while chunk := output.read(65536):
                    yield chunk
            finally:
                output.close()

        return StreamingResponse(chunks(), media_type="text/csv" if format == "csv" else "application/json",
                                 headers={"Content-Disposition": f"attachment; filename=users_export.{format}",
                                          "Cache-Control": "no-store"},
                                 background=BackgroundTask(output.close))

    return router
