"""API endpoints for managing FBNumber and pipeline runtime settings."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import ApiErrorResponse
from fb_data_pipeline.config import load_pipeline_settings

ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
}



class FBNumberSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_url: str
    api_token: str
    timeout_seconds: float
    max_retries: int
    default_country_code: str
    is_configured: bool


class FBNumberSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_url: str = Field(min_length=1, max_length=1024)
    api_token: str = Field(min_length=1, max_length=2048)
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    max_retries: int = Field(default=2, ge=0, le=10)
    default_country_code: str = Field(default="84", min_length=1, max_length=10)


class FBNumberTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_url: str | None = None
    api_token: str | None = None
    test_uid: str = Field(default="4", min_length=1, max_length=64)


class FBNumberTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    status_code: int
    latency_ms: float
    message: str
    raw_response: str


def update_env_file(updates: dict[str, str], env_path: Path | str = ".env") -> None:
    path = Path(env_path)
    lines: list[str] = []
    existing_keys: set[str] = set()

    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, _ = stripped.split("=", 1)
                key = key.strip()
                if key in updates:
                    lines.append(f"{key}={updates[key]}")
                    existing_keys.add(key)
                    continue
            lines.append(line)

    for key, value in updates.items():
        if key not in existing_keys:
            lines.append(f"{key}={value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value


def create_settings_router(*, auth: ApiKeyAuth) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/settings",
        tags=["settings"],
        dependencies=[Depends(auth)],
    )

    @router.get("/fbnumber", response_model=FBNumberSettingsResponse, responses=ERROR_RESPONSES)
    def get_fbnumber_settings() -> FBNumberSettingsResponse:
        settings = load_pipeline_settings()
        return FBNumberSettingsResponse(
            api_url=settings.fb_number_api_url,
            api_token=settings.fb_number_api_token,
            timeout_seconds=settings.fb_number_timeout_seconds,
            max_retries=settings.fb_number_max_retries,
            default_country_code=settings.default_country_code,
            is_configured=bool(settings.fb_number_api_url and settings.fb_number_api_token),
        )

    @router.post("/fbnumber", response_model=FBNumberSettingsResponse, responses=ERROR_RESPONSES)
    def update_fbnumber_settings(request: FBNumberSettingsUpdateRequest) -> FBNumberSettingsResponse:
        updates = {
            "FB_NUMBER_API_URL": request.api_url.strip(),
            "FB_NUMBER_API_TOKEN": request.api_token.strip(),
            "FB_NUMBER_TIMEOUT_SECONDS": str(request.timeout_seconds),
            "FB_NUMBER_MAX_RETRIES": str(request.max_retries),
            "PIPELINE_DEFAULT_COUNTRY_CODE": request.default_country_code.strip(),
        }
        update_env_file(updates)
        settings = load_pipeline_settings()
        return FBNumberSettingsResponse(
            api_url=settings.fb_number_api_url,
            api_token=settings.fb_number_api_token,
            timeout_seconds=settings.fb_number_timeout_seconds,
            max_retries=settings.fb_number_max_retries,
            default_country_code=settings.default_country_code,
            is_configured=bool(settings.fb_number_api_url and settings.fb_number_api_token),
        )

    @router.post("/fbnumber/test", response_model=FBNumberTestResponse, responses=ERROR_RESPONSES)
    def test_fbnumber_connection(request: FBNumberTestRequest) -> FBNumberTestResponse:
        current_settings = load_pipeline_settings()
        url = (request.api_url or current_settings.fb_number_api_url).strip()
        token = (request.api_token or current_settings.fb_number_api_token).strip()

        if not url or not token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="FB_NUMBER_API_URL và FB_NUMBER_API_TOKEN là bắt buộc để kiểm tra.",
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = {"uid": request.test_uid}

        start_time = time.monotonic()
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.post(url, json=payload, headers=headers)
                latency = round((time.monotonic() - start_time) * 1000, 2)
                success = res.status_code in {200, 201, 202, 404}
                msg = f"Kết nối thành công (HTTP {res.status_code})" if success else f"API trả về lỗi (HTTP {res.status_code})"
                return FBNumberTestResponse(
                    success=success,
                    status_code=res.status_code,
                    latency_ms=latency,
                    message=msg,
                    raw_response=res.text[:500],
                )
        except Exception as err:
            latency = round((time.monotonic() - start_time) * 1000, 2)
            return FBNumberTestResponse(
                success=False,
                status_code=0,
                latency_ms=latency,
                message=f"Không thể kết nối đến máy chủ FBNumber: {str(err)}",
                raw_response="",
            )

    return router
