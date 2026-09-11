from __future__ import annotations

import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict


class AppVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_version: str
    min_supported_version: str
    download_url: str
    release_notes: str
    release_date: str


def _find_dist_zip() -> Path | None:
    candidates = [
        Path("dist/lead-finder-latest.zip"),
        Path("runtime/downloads/lead-finder-latest.zip"),
        Path("../lead-finder/.output/lead-finder-0.1.0-chrome-dev.zip"),
        Path("../lead-finder/.output/lead-finder-0.1.0-chrome.zip"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    output_dir = Path("../lead-finder/.output")
    if output_dir.is_dir():
        for zip_file in sorted(output_dir.glob("*.zip"), reverse=True):
            if zip_file.is_file():
                return zip_file
    return None


def create_app_version_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/app", tags=["version"])

    @router.get("/version", response_model=AppVersionResponse)
    def get_app_version() -> AppVersionResponse:
        latest = os.environ.get("LATEST_EXTENSION_VERSION", "0.1.0").strip()
        min_supported = os.environ.get("MIN_SUPPORTED_EXTENSION_VERSION", "0.1.0").strip()
        download_url = os.environ.get(
            "EXTENSION_DOWNLOAD_URL",
            "/api/v1/app/download",
        ).strip()
        release_notes = os.environ.get(
            "EXTENSION_RELEASE_NOTES",
            "Cập nhật tối ưu hoá tính năng tra cứu, sửa lỗi và cải tiến độ ổn định.",
        ).strip()
        release_date = os.environ.get("EXTENSION_RELEASE_DATE", "2026-09-11").strip()

        return AppVersionResponse(
            latest_version=latest,
            min_supported_version=min_supported,
            download_url=download_url,
            release_notes=release_notes,
            release_date=release_date,
        )

    @router.get("/download")
    def download_latest_extension():
        zip_path = _find_dist_zip()
        if zip_path is None or not zip_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chưa có file nén cập nhật trên máy chủ.",
            )
        return FileResponse(
            str(zip_path),
            media_type="application/zip",
            filename="lead-finder-latest.zip",
        )

    return router
