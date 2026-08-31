from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from fb_crawl.api.app import create_app
from fb_crawl.api.config import ApiSettings
from fb_crawl.composition.product import ProductServices
from fb_crawl.auth.tokens import TokenService
from fb_crawl.auth.rate_limit import RateLimitService
from tests.unit.api.product_auth_fakes import (
    INSTALLATION_ID,
    NOW,
    ProductAuthServiceFake,
    ProductRepositoryFake,
    SESSION_ID,
)


API_KEY = "a" * 32
WEB_ORIGIN = "https://leads.example.com"


def product_client(
    *,
    license_service=None,
    entitlement_service=None,
    quota_service=None,
    contact_lookup_service=None,
    rate_limit_policies=None,
) -> tuple[TestClient, ProductRepositoryFake, ProductAuthServiceFake, str]:
    repository = ProductRepositoryFake()
    tokens = TokenService(jwt_secret="j" * 32, token_hmac_secret="h" * 32)
    access = tokens.issue_access(7, SESSION_ID, 9, NOW)
    auth_service = ProductAuthServiceFake(access, repository)
    services = ProductServices(
        auth_service,
        repository,
        tokens,
        rate_limiter=RateLimitService(
            repository,
            "r" * 32,
            policies=rate_limit_policies,
        ),
        license_service=license_service,
        entitlement_service=entitlement_service,
        quota_service=quota_service,
        contact_lookup_service=contact_lookup_service,
    )
    app = create_app(
        ApiSettings(api_key=API_KEY, cors_origins=(WEB_ORIGIN,)),
        job_service=object(),
        job_repository=object(),
        user_repository=object(),
        readiness=lambda _migration: True,
        product_services=services,
        clock=lambda: NOW,
    )
    return TestClient(app, raise_server_exceptions=False), repository, auth_service, access


def test_product_login_does_not_require_internal_api_key() -> None:
    client, _repository, auth_service, _access = product_client()

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "person@example.com",
            "password": "correct horse battery staple",
            "installation_id": str(INSTALLATION_ID),
            "device_name": "Chrome",
            "transport": "extension",
        },
    )

    assert response.status_code == 200
    assert response.json()["refresh_token"] == "opaque-refresh-token"
    assert auth_service.calls == [("login", "person@example.com")]


def test_existing_and_unknown_api_routes_still_require_internal_api_key() -> None:
    client, _repository, _auth_service, _access = product_client()

    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/protected-probe").status_code == 401
    allowed = client.get("/api/v1/jobs", headers={"X-API-Key": API_KEY})
    assert allowed.status_code != 401


def test_web_login_sets_secure_refresh_and_csrf_cookies_without_json_refresh() -> None:
    client, _repository, _auth_service, _access = product_client()

    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": WEB_ORIGIN},
        json={
            "email": "person@example.com",
            "password": "correct horse battery staple",
            "installation_id": str(INSTALLATION_ID),
            "device_name": "Chrome",
            "transport": "web",
        },
    )

    assert response.status_code == 200
    assert response.json()["refresh_token"] is None
    cookies = response.headers.get_list("set-cookie")
    assert any("lead_finder_refresh=" in item and "HttpOnly" in item and "Secure" in item for item in cookies)
    assert any("lead_finder_csrf=" in item and "Secure" in item for item in cookies)


def test_cookie_authenticated_state_change_requires_origin_bound_csrf() -> None:
    client, _repository, _auth_service, access = product_client()
    client.cookies.set("lead_finder_access", access)
    client.cookies.set("lead_finder_csrf", "csrf-value")

    denied = client.delete(
        "/api/v1/account",
        headers={
            "Origin": WEB_ORIGIN,
            "X-Installation-ID": str(INSTALLATION_ID),
        },
    )
    allowed = client.delete(
        "/api/v1/account",
        headers={
            "Origin": WEB_ORIGIN,
            "X-CSRF-Token": "csrf-value",
            "X-Installation-ID": str(INSTALLATION_ID),
        },
    )

    assert denied.status_code == 403
    assert denied.json()["code"] == "csrf_validation_failed"
    assert allowed.status_code == 200


def test_extension_bearer_must_match_installation_header() -> None:
    client, _repository, _auth_service, access = product_client()

    missing = client.get(
        "/api/v1/account/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    wrong = client.get(
        "/api/v1/account/me",
        headers={
            "Authorization": f"Bearer {access}",
            "X-Installation-ID": "99999999-9999-4999-8999-999999999999",
        },
    )
    allowed = client.get(
        "/api/v1/account/me",
        headers={
            "Authorization": f"Bearer {access}",
            "X-Installation-ID": str(INSTALLATION_ID),
        },
    )

    assert missing.status_code == wrong.status_code == 401
    assert allowed.status_code == 200


def test_web_logout_revokes_refresh_session_when_access_cookie_is_expired() -> None:
    client, _repository, auth_service, _access = product_client()
    client.cookies.set("lead_finder_refresh", "opaque-refresh-token", path="/api/v1/auth")
    client.cookies.set("lead_finder_csrf", "csrf-value")

    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": WEB_ORIGIN,
            "X-CSRF-Token": "csrf-value",
            "X-Installation-ID": str(INSTALLATION_ID),
        },
        json={"refresh_token": None, "transport": "web"},
    )

    assert response.status_code == 200
    assert ("logout_refresh", "opaque-refresh-token") in auth_service.calls
    cookies = response.headers.get_list("set-cookie")
    assert any("lead_finder_refresh=" in item and "Max-Age=0" in item for item in cookies)
