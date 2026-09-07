"""API endpoints for managing FBNumber and pipeline runtime settings."""

from __future__ import annotations

import os
import time
from datetime import datetime
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
    api_token_configured: bool
    timeout_seconds: float
    max_retries: int
    default_country_code: str
    is_configured: bool


class FBNumberSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_url: str = Field(min_length=1, max_length=1024)
    api_token: str | None = Field(default=None, max_length=2048)
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


class ProviderHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    configured: bool
    last_success_at: datetime | None
    safe_error_code: str
    updated_at: datetime | None


class WorkerSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cooldown_seconds: int
    navigation_delay_seconds: int
    job_timeout_seconds: int
    rate_limit_cooldown_seconds: int
    account_status: str
    cooldown_until: str | None


class WorkerSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cooldown_seconds: int = Field(default=3600, ge=0, le=86400)
    navigation_delay_seconds: int = Field(default=8, ge=8, le=120)
    job_timeout_seconds: int = Field(default=1800, ge=10, le=1800)
    rate_limit_cooldown_seconds: int = Field(default=21600, ge=0, le=604800)


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


def read_env_file_values(env_path: Path | str = ".env") -> dict[str, str]:
    path = Path(env_path)
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def effective_fbnumber_config() -> dict[str, str]:
    settings = load_pipeline_settings()
    env_file = read_env_file_values()
    return {
        "api_url": (
            os.environ.get("FB_NUMBER_API_URL")
            or env_file.get("FB_NUMBER_API_URL")
            or settings.fb_number_api_url
        ).strip(),
        "api_token": (
            os.environ.get("FB_NUMBER_API_TOKEN")
            or env_file.get("FB_NUMBER_API_TOKEN")
            or settings.fb_number_api_token
        ).strip(),
        "timeout_seconds": (
            os.environ.get("FB_NUMBER_TIMEOUT_SECONDS")
            or env_file.get("FB_NUMBER_TIMEOUT_SECONDS")
            or str(settings.fb_number_timeout_seconds)
        ).strip(),
        "max_retries": (
            os.environ.get("FB_NUMBER_MAX_RETRIES")
            or env_file.get("FB_NUMBER_MAX_RETRIES")
            or str(settings.fb_number_max_retries)
        ).strip(),
        "default_country_code": (
            os.environ.get("PIPELINE_DEFAULT_COUNTRY_CODE")
            or env_file.get("PIPELINE_DEFAULT_COUNTRY_CODE")
            or settings.default_country_code
        ).strip(),
    }


def effective_worker_config() -> dict[str, int]:
    env_file = read_env_file_values()
    cooldown_str = os.environ.get("CRAWL_WORKER_COOLDOWN_SECONDS") or env_file.get("CRAWL_WORKER_COOLDOWN_SECONDS")
    delay_str = os.environ.get("CRAWL_WORKER_NAVIGATION_DELAY_SECONDS") or env_file.get("CRAWL_WORKER_NAVIGATION_DELAY_SECONDS")
    timeout_str = os.environ.get("CRAWL_WORKER_JOB_TIMEOUT_SECONDS") or env_file.get("CRAWL_WORKER_JOB_TIMEOUT_SECONDS")
    rate_limit_cooldown_str = os.environ.get("CRAWL_WORKER_RATE_LIMIT_COOLDOWN_SECONDS") or env_file.get("CRAWL_WORKER_RATE_LIMIT_COOLDOWN_SECONDS")

    return {
        "cooldown_seconds": max(0, int(cooldown_str)) if cooldown_str and cooldown_str.isdigit() else 3600,
        "navigation_delay_seconds": max(8, int(delay_str)) if delay_str and delay_str.isdigit() else 8,
        "job_timeout_seconds": min(1800, max(10, int(timeout_str))) if timeout_str and timeout_str.isdigit() else 1800,
        "rate_limit_cooldown_seconds": max(0, int(rate_limit_cooldown_str)) if rate_limit_cooldown_str and rate_limit_cooldown_str.isdigit() else 21600,
    }


def create_settings_router(
    job_repository: object = None,
    *,
    auth: ApiKeyAuth,
    provider_health_repository: object | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/settings",
        tags=["settings"],
        dependencies=[Depends(auth)],
    )

    @router.get("/fbnumber", response_model=FBNumberSettingsResponse, responses=ERROR_RESPONSES)
    def get_fbnumber_settings() -> FBNumberSettingsResponse:
        config = effective_fbnumber_config()
        token = config["api_token"]
        return FBNumberSettingsResponse(
            api_url=config["api_url"],
            api_token=token,
            api_token_configured=bool(token),
            timeout_seconds=float(config["timeout_seconds"]),
            max_retries=int(config["max_retries"]),
            default_country_code=config["default_country_code"],
            is_configured=bool(config["api_url"] and token),
        )

    @router.post("/fbnumber", response_model=FBNumberSettingsResponse, responses=ERROR_RESPONSES)
    def update_fbnumber_settings(request: FBNumberSettingsUpdateRequest) -> FBNumberSettingsResponse:
        current_config = effective_fbnumber_config()
        token = (request.api_token or current_config["api_token"]).strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="FB_NUMBER_API_TOKEN is required before FBNumber can be enabled.",
            )
        updates = {
            "FB_NUMBER_API_URL": request.api_url.strip(),
            "FB_NUMBER_API_TOKEN": token,
            "FB_NUMBER_TIMEOUT_SECONDS": str(request.timeout_seconds),
            "FB_NUMBER_MAX_RETRIES": str(request.max_retries),
            "PIPELINE_DEFAULT_COUNTRY_CODE": request.default_country_code.strip(),
        }
        update_env_file(updates)
        config = effective_fbnumber_config()
        token = config["api_token"]
        return FBNumberSettingsResponse(
            api_url=config["api_url"],
            api_token=token,
            api_token_configured=bool(token),
            timeout_seconds=float(config["timeout_seconds"]),
            max_retries=int(config["max_retries"]),
            default_country_code=config["default_country_code"],
            is_configured=bool(config["api_url"] and token),
        )

    @router.post("/fbnumber/test", response_model=FBNumberTestResponse, responses=ERROR_RESPONSES)
    def test_fbnumber_connection(request: FBNumberTestRequest) -> FBNumberTestResponse:
        current_config = effective_fbnumber_config()
        url = (request.api_url or current_config["api_url"]).strip()
        token = (request.api_token or current_config["api_token"]).strip()

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
                    raw_response="",
                )
        except Exception:
            latency = round((time.monotonic() - start_time) * 1000, 2)
            return FBNumberTestResponse(
                success=False,
                status_code=0,
                latency_ms=latency,
                message="Could not connect to the FBNumber server.",
                raw_response="",
            )

    @router.get(
        "/fbnumber/health",
        response_model=ProviderHealthResponse,
        responses=ERROR_RESPONSES,
    )
    def get_fbnumber_health() -> ProviderHealthResponse:
        config = effective_fbnumber_config()
        value = (
            provider_health_repository.get("fbnumber")
            if provider_health_repository is not None
            else None
        )
        return ProviderHealthResponse(
            provider="fbnumber",
            configured=bool(config["api_url"] and config["api_token"]),
            last_success_at=(value.last_success_at if value is not None else None),
            safe_error_code=(value.safe_error_code if value is not None else ""),
            updated_at=(value.updated_at if value is not None else None),
        )

    @router.get("/worker", response_model=WorkerSettingsResponse, responses=ERROR_RESPONSES)
    def get_worker_settings() -> WorkerSettingsResponse:
        config = effective_worker_config()
        status_name = "ready"
        cooldown_until_str = None
        if job_repository is not None:
            try:
                if hasattr(job_repository, "get_account"):
                    account = job_repository.get_account("default")
                    if account is not None and hasattr(account, "status"):
                        st = getattr(account.status, "value", account.status)
                        if isinstance(st, str):
                            status_name = st
                        cd = getattr(account, "cooldown_until", None)
                        if isinstance(cd, datetime):
                            cooldown_until_str = cd.isoformat()
                        elif isinstance(cd, str):
                            cooldown_until_str = cd
            except Exception:
                pass
        else:
            try:
                settings = load_pipeline_settings()
                from fb_data_pipeline.repositories.jobs import JobRepository
                repo = JobRepository(settings.database_url)
                account = repo.get_account("default")
                if account is not None:
                    status_name = account.status.value
                    if account.cooldown_until:
                        cooldown_until_str = account.cooldown_until.isoformat()
            except Exception:
                pass

        return WorkerSettingsResponse(
            cooldown_seconds=config["cooldown_seconds"],
            navigation_delay_seconds=config["navigation_delay_seconds"],
            job_timeout_seconds=config["job_timeout_seconds"],
            rate_limit_cooldown_seconds=config["rate_limit_cooldown_seconds"],
            account_status=status_name,
            cooldown_until=cooldown_until_str,
        )

    @router.post("/worker", response_model=WorkerSettingsResponse, responses=ERROR_RESPONSES)
    def update_worker_settings(request: WorkerSettingsUpdateRequest) -> WorkerSettingsResponse:
        updates = {
            "CRAWL_WORKER_COOLDOWN_SECONDS": str(request.cooldown_seconds),
            "CRAWL_WORKER_NAVIGATION_DELAY_SECONDS": str(request.navigation_delay_seconds),
            "CRAWL_WORKER_JOB_TIMEOUT_SECONDS": str(request.job_timeout_seconds),
            "CRAWL_WORKER_RATE_LIMIT_COOLDOWN_SECONDS": str(request.rate_limit_cooldown_seconds),
        }
        update_env_file(updates)
        return get_worker_settings()

    @router.post("/reset-cooldown", responses=ERROR_RESPONSES)
    def reset_cooldown() -> dict[str, str]:
        if job_repository is not None:
            try:
                if hasattr(job_repository, "reset_account_cooldown"):
                    job_repository.reset_account_cooldown("default")
                return {
                    "status": "success",
                    "message": "Đã reset trạng thái Cooldown thành công. Nick đã sẵn sàng chạy Job tiếp theo.",
                }
            except Exception as err:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Không thể reset cooldown: {str(err)}",
                )
        try:
            settings = load_pipeline_settings()
            from fb_data_pipeline.repositories.jobs import JobRepository
            repo = JobRepository(settings.database_url)
            repo.reset_account_cooldown("default")
            return {
                "status": "success",
                "message": "Đã reset trạng thái Cooldown thành công. Nick đã sẵn sàng chạy Job tiếp theo.",
            }
        except Exception as err:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Không thể reset cooldown: {str(err)}",
            )

    return router
