from __future__ import annotations

from datetime import UTC, datetime
import secrets
from typing import Callable

from fastapi import APIRouter, Depends, Request, Response

from fb_crawl.api.dependencies import CurrentAccount, ProductAccountAuth, validate_cookie_csrf
from fb_crawl.api.product_schemas import (
    AuthTokenResponse,
    EmailRequest,
    GenericAcceptedResponse,
    GoogleLoginRequest,
    LoginRequest,
    LogoutRequest,
    ReauthenticateRequest,
    RefreshRequest,
    RegisterRequest,
    RegistrationResponse,
    ResetPasswordRequest,
    TokenRequest,
    VerifyEmailRequest,
)
from fb_crawl.auth.service import AccountAuthService, AuthTokens
from fb_crawl.core.exceptions import ValidationError


def create_product_auth_router(
    auth_service: AccountAuthService,
    current_auth: ProductAccountAuth,
    *,
    allowed_origins: tuple[str, ...],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["product-auth"])

    @router.post("/register", response_model=RegistrationResponse)
    def register(payload: RegisterRequest, request: Request) -> RegistrationResponse:
        result = auth_service.register(
            payload.email,
            payload.password,
            clock(),
            ip_address=_client_ip(request),
        )
        return RegistrationResponse(
            account_id=result.account_id,
            email=result.email,
            verification_required=result.verification_required,
        )

    @router.post("/verify-email", response_model=GenericAcceptedResponse)
    def verify_email(
        payload: VerifyEmailRequest, request: Request
    ) -> GenericAcceptedResponse:
        token_or_code = (payload.code or payload.token or "").strip()
        if not token_or_code:
            raise ValidationError("A verification code or token is required.")
        auth_service.verify_email(
            token_or_code,
            clock(),
            ip_address=_client_ip(request),
            email=payload.email,
        )
        return GenericAcceptedResponse()

    @router.post("/resend-verification", response_model=GenericAcceptedResponse)
    def resend_verification(
        payload: EmailRequest, request: Request
    ) -> GenericAcceptedResponse:
        auth_service.resend_verification(
            payload.email, clock(), ip_address=_client_ip(request)
        )
        return GenericAcceptedResponse()

    @router.post("/login", response_model=AuthTokenResponse)
    def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
    ) -> AuthTokenResponse:
        tokens = auth_service.login(
            payload.email,
            payload.password,
            payload.installation_id,
            payload.device_name,
            clock(),
            ip_address=_client_ip(request),
        )
        return _transport_tokens(
            tokens,
            payload.transport,
            request,
            response,
            frozenset(allowed_origins),
        )

    @router.post("/google", response_model=AuthTokenResponse)
    def login_with_google(
        payload: GoogleLoginRequest,
        request: Request,
        response: Response,
    ) -> AuthTokenResponse:
        tokens = auth_service.login_with_google(
            payload.id_token,
            payload.installation_id,
            payload.device_name,
            clock(),
            ip_address=_client_ip(request),
        )
        return _transport_tokens(
            tokens,
            payload.transport,
            request,
            response,
            frozenset(allowed_origins),
        )

    @router.post("/refresh", response_model=AuthTokenResponse)
    def refresh(
        payload: RefreshRequest,
        request: Request,
        response: Response,
    ) -> AuthTokenResponse:
        raw_refresh = payload.refresh_token
        if payload.transport == "web":
            validate_cookie_csrf(request, frozenset(allowed_origins))
            raw_refresh = request.cookies.get("lead_finder_refresh")
        if raw_refresh is None:
            from fb_crawl.api.dependencies import ProductAuthenticationError

            raise ProductAuthenticationError()
        tokens = auth_service.refresh(
            raw_refresh,
            payload.installation_id,
            clock(),
            ip_address=_client_ip(request),
        )
        return _transport_tokens(
            tokens,
            payload.transport,
            request,
            response,
            frozenset(allowed_origins),
        )

    @router.post("/forgot-password", response_model=GenericAcceptedResponse)
    def forgot_password(
        payload: EmailRequest, request: Request
    ) -> GenericAcceptedResponse:
        auth_service.forgot_password(
            payload.email, clock(), ip_address=_client_ip(request)
        )
        return GenericAcceptedResponse()

    @router.post("/reset-password", response_model=GenericAcceptedResponse)
    def reset_password(
        payload: ResetPasswordRequest, request: Request
    ) -> GenericAcceptedResponse:
        auth_service.reset_password(
            payload.token,
            payload.new_password,
            clock(),
            ip_address=_client_ip(request),
        )
        return GenericAcceptedResponse()

    @router.post("/logout", response_model=GenericAcceptedResponse)
    def logout(
        request: Request,
        response: Response,
        payload: LogoutRequest | None = None,
    ) -> GenericAcceptedResponse:
        transport = payload.transport if payload is not None else (
            "web" if request.cookies.get("lead_finder_refresh") else "extension"
        )
        raw_refresh = payload.refresh_token if payload is not None else None
        if transport == "web":
            validate_cookie_csrf(request, frozenset(allowed_origins))
            raw_refresh = request.cookies.get("lead_finder_refresh")
        if raw_refresh:
            auth_service.logout_refresh(raw_refresh, clock())
        response.delete_cookie("lead_finder_access", path="/")
        response.delete_cookie("lead_finder_refresh", path="/api/v1/auth")
        response.delete_cookie("lead_finder_csrf", path="/")
        return GenericAcceptedResponse()

    @router.post("/reauthenticate", response_model=GenericAcceptedResponse)
    def reauthenticate(
        payload: ReauthenticateRequest,
        request: Request,
        current: CurrentAccount = Depends(current_auth),
    ) -> GenericAcceptedResponse:
        return auth_service.reauthenticate(
            current.account.id,
            current.session.id,
            payload.password,
            clock(),
            ip_address=_client_ip(request),
        )

    return router


def _transport_tokens(
    tokens: AuthTokens,
    transport: str,
    request: Request,
    response: Response,
    allowed_origins: frozenset[str],
) -> AuthTokenResponse:
    refresh_token: str | None = tokens.refresh_token
    if transport == "web":
        origin = request.headers.get("Origin", "")
        if origin not in allowed_origins:
            from fb_crawl.api.dependencies import CsrfValidationError

            raise CsrfValidationError()
        csrf_token = secrets.token_urlsafe(24)
        response.set_cookie(
            "lead_finder_access",
            tokens.access_token,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=900,
        )
        response.set_cookie(
            "lead_finder_refresh",
            tokens.refresh_token,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/api/v1/auth",
            max_age=30 * 24 * 60 * 60,
        )
        response.set_cookie(
            "lead_finder_csrf",
            csrf_token,
            secure=True,
            httponly=False,
            samesite="strict",
            path="/",
            max_age=30 * 24 * 60 * 60,
        )
        refresh_token = None
    if tokens.access_expires_at is None:
        raise RuntimeError("Auth service omitted access expiry.")
    return AuthTokenResponse(
        access_token=tokens.access_token,
        refresh_token=refresh_token,
        access_expires_at=tokens.access_expires_at,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"
