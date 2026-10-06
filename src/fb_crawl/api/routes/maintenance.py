from fastapi import APIRouter, Depends, HTTPException, Request, Response

from fb_crawl.accounts.models import AccountRole
from fb_crawl.api.dependencies import CurrentAccount
from fb_crawl.maintenance import MaintenanceStatus, MaintenanceUpdate


def create_maintenance_router(current_account_dependency=None) -> APIRouter:
    router = APIRouter(tags=["maintenance"])

    @router.get("/api/v1/app/maintenance", response_model=MaintenanceStatus)
    def get_status(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return request.app.state.maintenance_store.read()

    if current_account_dependency is not None:
        @router.post("/api/v1/admin/maintenance", response_model=MaintenanceStatus)
        def update_status(payload: MaintenanceUpdate, request: Request, response: Response,
                          current: CurrentAccount = Depends(current_account_dependency)):
            if current.account.role is not AccountRole.ADMIN:
                raise HTTPException(status_code=403, detail="Administrator access required.")
            response.headers["Cache-Control"] = "no-store"
            return request.app.state.maintenance_store.write(payload.enabled, payload.message)

    return router
