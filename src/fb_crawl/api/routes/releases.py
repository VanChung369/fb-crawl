from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response

from fb_crawl.accounts.models import AccountRole
from fb_crawl.api.dependencies import CurrentAccount
from fb_crawl.releases import MAX_RELEASE_BYTES, ReleaseError, UpdatePolicy


def create_release_admin_router(current_account_dependency):
    router = APIRouter(prefix="/api/v1/admin/releases", tags=["releases"])

    def admin(current: CurrentAccount = Depends(current_account_dependency)):
        if current.account.role is not AccountRole.ADMIN:
            raise HTTPException(status_code=403, detail="Administrator access required.")
        return current

    @router.get("")
    def list_releases(request: Request, _=Depends(admin)):
        releases, policy = request.app.state.release_service.list()
        return {"releases": releases, "policy": policy}

    @router.post("", status_code=201)
    async def upload(request: Request, _=Depends(admin)):
        if request.headers.get("content-type", "").split(";")[0].lower() != "application/zip":
            raise ReleaseError("Chỉ nhận file ZIP.", 415)
        service = request.app.state.release_service
        service.directory.mkdir(parents=True, exist_ok=True)
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=service.directory, suffix=".upload", delete=False) as handle:
                path = Path(handle.name)
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_RELEASE_BYTES:
                        raise ReleaseError("File ZIP vượt quá 25 MB.", 413)
                    await run_in_threadpool(handle.write, chunk)
            return await run_in_threadpool(service.upload, request.headers.get("x-release-version", ""), path)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    @router.put("/policy")
    def save_policy(payload: UpdatePolicy, request: Request, _=Depends(admin)):
        return request.app.state.release_service.save_policy(payload)

    @router.delete("/{release_id}", status_code=204)
    def delete_release(release_id: UUID, request: Request, unpublish: bool = False, _=Depends(admin)):
        request.app.state.release_service.delete(release_id, unpublish=unpublish)
        return Response(status_code=204)

    return router


def create_release_download_router():
    router = APIRouter(prefix="/api/v1/app", tags=["version"])

    @router.get("/releases/{release_id}/download")
    def download(release_id: UUID, request: Request):
        service = request.app.state.release_service
        releases, policy = service.list()
        active = next((r for r in releases if r.id == release_id), None)
        if active is None or policy.active_release_id != release_id or not service.path(release_id).is_file():
            raise HTTPException(status_code=404, detail="Bản phát hành không khả dụng.")
        return FileResponse(service.path(release_id), media_type="application/zip", filename=f"lead-finder-{active.version}-chrome.zip", headers={"Cache-Control": "no-store"})

    return router
