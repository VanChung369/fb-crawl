"""Session pool management routes."""

from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter, Depends, status

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    ApiErrorResponse,
    SessionExtractRequest,
    SessionImportRequest,
    SessionItemResponse,
    SessionListResponse,
)
from fb_crawl.core.session_pool import SessionPool

ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
}


def create_sessions_router(session_pool: SessionPool, sessions_dir: Path, auth: ApiKeyAuth) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/sessions",
        tags=["sessions"],
        dependencies=[Depends(auth)],
    )

    @router.get("", response_model=SessionListResponse, responses=ERROR_RESPONSES)
    def list_sessions() -> SessionListResponse:
        items = [
            SessionItemResponse(
                name=s.path.name,
                proxy=s.proxy,
                status=s.status.value,
                success_count=s.success_count,
                failure_count=s.failure_count,
                is_available=s.is_available,
            )
            for s in session_pool._sessions
        ]
        return SessionListResponse(
            total_count=session_pool.total_count,
            available_count=len(session_pool.available_sessions),
            items=items,
        )

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionItemResponse, responses=ERROR_RESPONSES)
    def import_session(request: SessionImportRequest) -> SessionItemResponse:
        import time
        from fastapi import HTTPException
        from fb_crawl.core.session_parser import parse_cookie_input

        inferred_uid, cookies = parse_cookie_input(request.cookies)
        if not cookies:
            raise HTTPException(status_code=400, detail="Không thể nhận diện danh sách Cookie từ dữ liệu nhập vào.")

        sessions_dir.mkdir(parents=True, exist_ok=True)
        raw_name = request.name or (f"fb_{inferred_uid}" if inferred_uid else f"session_{int(time.time())}")
        filename = raw_name if raw_name.endswith(".json") else f"{raw_name}.json"
        session_file = sessions_dir / filename
        session_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

        managed = session_pool.add_session(session_file, proxy=request.proxy)
        return SessionItemResponse(
            name=managed.path.name,
            proxy=managed.proxy,
            status=managed.status.value,
            success_count=managed.success_count,
            failure_count=managed.failure_count,
            is_available=managed.is_available,
        )


    @router.post("/extract", status_code=status.HTTP_201_CREATED, response_model=SessionItemResponse, responses=ERROR_RESPONSES)
    def extract_session(request: SessionExtractRequest) -> SessionItemResponse:
        from fb_crawl.adapters.browser.session_extractor import extract_session_from_credentials

        sessions_dir.mkdir(parents=True, exist_ok=True)
        filename = request.name if request.name.endswith(".json") else f"{request.name}.json"
        session_file = sessions_dir / filename

        extract_session_from_credentials(
            email=request.email,
            password=request.password,
            two_factor_code=request.two_factor_code,
            proxy=request.proxy,
            output_path=session_file,
            headless=request.headless,
        )

        managed = session_pool.add_session(session_file, proxy=request.proxy)
        return SessionItemResponse(
            name=managed.path.name,
            proxy=managed.proxy,
            status=managed.status.value,
            success_count=managed.success_count,
            failure_count=managed.failure_count,
            is_available=managed.is_available,
        )

    return router

