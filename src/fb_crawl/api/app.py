from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from fb_crawl.api.config import ApiSettings
from fb_crawl.api.dependencies import (
    AUTH_ERROR_BODY,
    ApiAuthenticationError,
    ApiKeyAuth,
    CsrfValidationError,
    ProductAccountAuth,
    ProductAuthenticationError,
)
from fb_crawl.api.routes.health import ReadinessCheck, create_health_router
from fb_crawl.api.routes.account import create_account_router
from fb_crawl.api.routes.jobs import create_jobs_router
from fb_crawl.api.routes.users import UserNotFound, create_users_router
from fb_crawl.api.routes.proxies import create_proxies_router
from fb_crawl.api.routes.sessions import create_sessions_router
from fb_crawl.api.routes.stats import create_stats_router
from fb_crawl.api.routes.export import create_export_router
from fb_crawl.api.routes.settings import create_settings_router
from fb_crawl.core.session_pool import SessionPool
from fb_crawl.core.proxy_pool import ProxyPool
from fb_crawl.core.exceptions import FbCrawlError, ValidationError
from fb_crawl.api.safe_logging import log_unexpected_api_error
from fb_crawl.api.correlation import correlate_request
from fb_crawl.core.jobs import IdempotencyConflict, JobConflict, JobNotFound
from pathlib import Path
from fb_crawl.composition.product import ProductServices
from fb_crawl.accounts.repository import AccountNotFound, AdminAccountProtected, DeviceNotFound
from fb_crawl.auth.rate_limit import AuthRateLimited
from fb_crawl.auth.service import (
    AccountAlreadyRegistered,
    AccountUnavailable,
    DeviceBindingMismatch,
    EmailVerificationRequired,
    InvalidCredentials,
)


logger = logging.getLogger(__name__)


def create_app(
    settings: ApiSettings,
    job_service: Any,
    job_repository: Any,
    user_repository: Any,
    readiness: ReadinessCheck,
    *,
    proxy_pool: ProxyPool | None = None,
    session_pool: SessionPool | None = None,
    sessions_dir: Path | None = None,
    product_services: ProductServices | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """Build an API process from injected services without browser ownership."""

    auth = ApiKeyAuth(settings.api_key)
    resolved_sessions_dir = sessions_dir or Path("runtime/sessions")
    resolved_proxy_pool = proxy_pool or ProxyPool(file_path=Path("runtime/proxies.txt"))
    resolved_session_pool = session_pool or SessionPool(sessions_dir=resolved_sessions_dir, proxy_pool=resolved_proxy_pool)

    app = FastAPI(
        title="fb-crawl API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.job_service = job_service
    app.state.job_repository = job_repository
    app.state.user_repository = user_repository
    app.state.api_key_auth = auth
    app.state.proxy_pool = resolved_proxy_pool
    app.state.session_pool = resolved_session_pool
    app.state.product_services = product_services

    _install_exception_handlers(app)
    from fb_crawl.interaction_sessions.models import SessionError

    @app.exception_handler(SessionError)
    async def interaction_session_error(_request: Request, error: SessionError):
        return JSONResponse(status_code=error.status, content={"code": error.code, "message": error.safe_message})
    app.include_router(create_health_router(readiness))
    app.include_router(create_jobs_router(job_service, job_repository, auth))
    app.include_router(create_account_router(job_service, auth))
    app.include_router(create_users_router(user_repository, auth))
    app.include_router(create_proxies_router(resolved_proxy_pool, auth))
    app.include_router(
        create_sessions_router(
            resolved_session_pool,
            resolved_sessions_dir,
            auth,
            allow_local_browser_login=settings.host in {"127.0.0.1", "localhost", "::1"},
        )
    )
    app.include_router(create_stats_router(job_repository, user_repository, resolved_session_pool, resolved_proxy_pool, auth))
    app.include_router(create_export_router(user_repository, auth))
    app.include_router(create_settings_router(
        job_repository=job_repository,
        auth=auth,
        provider_health_repository=(
            product_services.provider_health_repository
            if product_services is not None
            else None
        ),
        product_services=product_services,
    ))

    if product_services is not None:
        from fb_crawl.api.routes.auth import create_product_auth_router
        from fb_crawl.api.routes.product_account import create_product_account_router

        product_auth = ProductAccountAuth(
            product_services.account_repository,
            product_services.token_service,
            allowed_origins=settings.cors_origins,
            entitlement_service=product_services.entitlement_service,
            clock=clock,
        )
        if product_services.interaction_session_service is not None:
            from fb_crawl.api.routes.interaction_sessions import create_interaction_sessions_router
            app.include_router(create_interaction_sessions_router(
                product_services.interaction_session_service, product_auth,
                lookup_service=product_services.interaction_session_lookup_service,
                rate_limiter=product_services.rate_limiter, clock=clock,
            ))
        if product_services.lead_repository is not None:
            from fb_crawl.api.routes.leads import create_leads_router
            app.include_router(create_leads_router(product_services.lead_repository, product_auth))
        app.include_router(
            create_product_auth_router(
                product_services.auth_service,
                product_auth,
                allowed_origins=settings.cors_origins,
                clock=clock,
            )
        )
        if (
            product_services.license_service is not None
            and product_services.entitlement_service is not None
            and product_services.rate_limiter is not None
        ):
            from fb_crawl.api.routes.licenses import create_license_router
            from fb_crawl.api.routes.product_admin import create_product_admin_router

            app.include_router(
                create_license_router(
                    product_services.license_service,
                    product_services.entitlement_service,
                    product_auth,
                    product_services.rate_limiter,
                    quota=product_services.quota_service,
                    clock=clock,
                )
            )
            app.include_router(
                create_product_admin_router(
                    product_services.account_repository,
                    product_services.license_service,
                    product_auth,
                    product_services.rate_limiter,
                    clock=clock,
                )
            )
        if (
            product_services.export_service is not None
            and product_services.rate_limiter is not None
        ):
            from fb_crawl.api.routes.product_exports import (
                create_product_export_router,
            )
            from fb_crawl.api.routes.product_worker_health import (
                create_product_worker_health_router,
            )

            app.include_router(
                create_product_export_router(
                    product_services.export_service,
                    product_auth,
                    product_services.rate_limiter,
                    clock=clock,
                )
            )
            app.include_router(
                create_product_worker_health_router(
                    product_services.export_service,
                    product_auth,
                    clock=clock,
                )
            )
        if product_services.metrics_repository is not None:
            from fb_crawl.api.routes.product_metrics import (
                create_product_metrics_router,
            )

            app.include_router(
                create_product_metrics_router(
                    product_services.metrics_repository,
                    product_auth,
                )
            )
        if (
            product_services.history_repository is not None
            and product_services.quota_service is not None
        ):
            from fb_crawl.api.routes.history import create_history_router

            app.include_router(
                create_history_router(
                    product_services.history_repository,
                    product_auth,
                    product_services.quota_service,
                    clock=clock,
                )
            )
        app.include_router(
            create_product_account_router(
                product_services.account_repository,
                product_auth,
                clock=clock,
            )
        )
        if (
            product_services.contact_lookup_service is not None
            and product_services.rate_limiter is not None
        ):
            from fb_crawl.api.routes.contacts import create_contact_router

            app.include_router(
                create_contact_router(
                    product_services.contact_lookup_service,
                    product_auth,
                    product_services.rate_limiter,
                    clock=clock,
                )
            )
        if (
            product_services.product_crawl_repository is not None
            and product_services.entitlement_service is not None
        ):
            from fb_crawl.api.routes.product_crawl_jobs import (
                create_product_crawl_router,
            )
            from fb_crawl.product_jobs.service import ProductCrawlJobService

            app.include_router(
                create_product_crawl_router(
                    ProductCrawlJobService(
                        product_services.product_crawl_repository,
                        product_services.entitlement_service,
                        job_service,
                        job_repository,
                        session_available=(
                            product_services.facebook_session_available
                        ),
                    ),
                    product_auth,
                    product_services.history_repository,
                    clock=clock,
                )
            )

    _install_api_authentication(app, auth)
    app.middleware("http")(correlate_request)

    if settings.docs_enabled:
        _install_protected_docs(app, auth)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "X-Installation-ID",
                "X-CSRF-Token",
                "X-API-Key",
                "Idempotency-Key",
                "Content-Type",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID", "Content-Disposition"],
        )

    ui_dir = Path(__file__).parents[2] / "fb_ui"
    if not ui_dir.is_dir():
        ui_dir = Path(__file__).parent / "static"

    if ui_dir.is_dir():
        from fastapi import APIRouter
        from fastapi.responses import FileResponse, Response
        from fastapi.staticfiles import StaticFiles

        # Use a router registered BEFORE the StaticFiles mount so these routes
        # take priority and return fresh content with no-cache headers.
        _no_cache_router = APIRouter()

        @_no_cache_router.get("/static/js/app.js")
        def serve_app_js():
            path = ui_dir / "js" / "app.js"
            if not path.is_file():
                return Response(status_code=404)
            return Response(
                content=path.read_bytes(),
                media_type="application/javascript",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )

        @_no_cache_router.get("/static/css/styles.css")
        def serve_styles_css():
            path = ui_dir / "css" / "styles.css"
            if not path.is_file():
                return Response(status_code=404)
            return Response(
                content=path.read_bytes(),
                media_type="text/css",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )

        @_no_cache_router.get("/")
        @_no_cache_router.get("/dashboard")
        def serve_dashboard():
            response = FileResponse(str(ui_dir / "index.html"))
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

        app.include_router(_no_cache_router)
        # Mount remaining static files (images, fonts, etc.) - JS/CSS handled above
        app.mount("/static", StaticFiles(directory=str(ui_dir)), name="static")

    return app


def _install_api_authentication(app: FastAPI, auth: ApiKeyAuth) -> None:
    @app.middleware("http")
    async def authenticate_api(request: Request, call_next: Callable):
        path = request.url.path
        is_preflight = (
            request.method == "OPTIONS"
            and "access-control-request-method" in request.headers
        )
        if _requires_internal_api_key(path) and not is_preflight:
            try:
                auth.verify(request.headers.get("X-API-Key"))
            except ApiAuthenticationError:
                return JSONResponse(
                    status_code=401,
                    content=AUTH_ERROR_BODY,
                    headers={"WWW-Authenticate": "ApiKey"},
                )
        return await call_next(request)


def _requires_internal_api_key(path: str) -> bool:
    if path == "/api/v1/account/default" or path.startswith(
        "/api/v1/account/default/"
    ):
        return True
    product_prefixes = (
        "/api/v1/auth",
        "/api/v1/account",
        "/api/v1/devices",
        "/api/v1/licenses",
        "/api/v1/admin",
        "/api/v1/contacts",
        "/api/v1/crawl-jobs",
        "/api/v1/history",
        "/api/v1/interaction-sessions",
        "/api/v1/exports",
        "/api/v1/worker-health",
    )
    if any(path == prefix or path.startswith(f"{prefix}/") for prefix in product_prefixes):
        return False
    return path == "/api/v1" or path.startswith("/api/v1/")


def _install_protected_docs(app: FastAPI, auth: ApiKeyAuth) -> None:
    @app.get(
        "/openapi.json",
        include_in_schema=False,
        dependencies=[Depends(auth)],
    )
    def openapi_schema() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get(
        "/docs",
        include_in_schema=False,
        dependencies=[Depends(auth)],
    )
    def swagger_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - Swagger UI",
        )


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "code": "request_validation_failed",
                "message": "Request validation failed.",
            },
        )

    @app.exception_handler(ApiAuthenticationError)
    async def authentication_error_handler(
        _request: Request,
        _error: ApiAuthenticationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content=AUTH_ERROR_BODY,
            headers={"WWW-Authenticate": "ApiKey"},
        )

    @app.exception_handler(ProductAuthenticationError)
    async def product_authentication_error_handler(
        _request: Request,
        _error: ProductAuthenticationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={
                "code": "product_unauthorized",
                "message": "Product authentication failed.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(CsrfValidationError)
    async def csrf_validation_error_handler(
        _request: Request,
        error: CsrfValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"code": error.code, "message": error.safe_message},
        )

    @app.exception_handler(FbCrawlError)
    async def fb_crawl_error_handler(
        _request: Request,
        error: FbCrawlError,
    ) -> JSONResponse:
        if isinstance(error, (JobNotFound, UserNotFound, AccountNotFound, DeviceNotFound)):
            status_code = 404
        elif isinstance(error, (IdempotencyConflict, JobConflict, AccountAlreadyRegistered)):
            status_code = 409
        elif isinstance(error, InvalidCredentials):
            status_code = 401
        elif isinstance(error, AuthRateLimited):
            status_code = 429
        elif isinstance(error, AdminAccountProtected):
            status_code = 403
        elif isinstance(
            error,
            (EmailVerificationRequired, AccountUnavailable, DeviceBindingMismatch),
        ):
            status_code = 403
        elif isinstance(error, ValidationError):
            status_code = 400
        else:
            status_code = 500
        return JSONResponse(
            status_code=status_code,
            content={"code": error.code, "message": error.safe_message},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request,
        _error: Exception,
    ) -> JSONResponse:
        log_unexpected_api_error(
            logger,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_server_error",
                "message": "Internal server error.",
            },
        )
