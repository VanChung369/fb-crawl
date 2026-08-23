"""Data export routes for CSV / JSON stream downloads."""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any
from fastapi import APIRouter, Depends, Query, Response

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import ApiErrorResponse
from fb_crawl.api.routes.users import query_user_page, user_response_values

ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
}


def create_export_router(user_repository: Any, auth: ApiKeyAuth) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/export",
        tags=["export"],
        dependencies=[Depends(auth)],
    )

    @router.get("/users", responses=ERROR_RESPONSES)
    def export_users(
        format: Annotated[str, Query()] = "csv",
        q: Annotated[str | None, Query(max_length=256)] = None,
        uid: Annotated[str | None, Query(max_length=128)] = None,
        username: Annotated[str | None, Query(max_length=256)] = None,
        phone: Annotated[str | None, Query(max_length=64)] = None,
        has_phone: Annotated[bool | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> Response:
        page = query_user_page(
            user_repository,
            q=q,
            uid=uid,
            username=username,
            phone=phone,
            has_phone=has_phone,
            limit=limit,
        )

        rows = [user_response_values(u) for u in page.items]

        if format.lower() == "json":
            import json

            content = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
            return Response(
                content=content,
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=users_export.json"},
            )

        # Default CSV export
        output = io.StringIO()
        fieldnames = [
            "id",
            "facebook_uid",
            "username",
            "name",
            "profile_url",
            "phone_1",
            "phone_2",
            "address",
            "birth_date",
            "gender",
            "created_at",
            "updated_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=users_export.csv"},
        )

    return router
