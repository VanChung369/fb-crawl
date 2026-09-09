from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from fb_crawl.api.app import create_app
from fb_crawl.api.config import ApiSettings
from fb_crawl.auth.google import GoogleAuthError, GoogleUserInfo
from fb_crawl.auth.rate_limit import RateLimitService
from fb_crawl.auth.service import AccountAuthService
from fb_crawl.auth.tokens import TokenService
from fb_crawl.composition.product import ProductServices
from tests.unit.api.product_auth_fakes import (
    INSTALLATION_ID,
    NOW,
    ProductRepositoryFake,
)


class FakeGoogleVerifier:
    def __init__(self, user_info: GoogleUserInfo | None = None, error: Exception | None = None):
        self.user_info = user_info
        self.error = error

    def verify_id_token(self, id_token: str) -> GoogleUserInfo:
        if self.error:
            raise self.error
        if self.user_info is None:
            raise GoogleAuthError("Token verification failed.")
        return self.user_info


class FakePasswordHasher:
    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, hashed: str, password: str) -> bool:
        return hashed == f"hashed:{password}"


class FakeEmailDelivery:
    def send_verification(self, email: str, url: str) -> None:
        pass

    def send_password_reset(self, email: str, url: str) -> None:
        pass


def make_test_app(verifier: FakeGoogleVerifier, repo: ProductRepositoryFake):
    tokens = TokenService(jwt_secret="j" * 32, token_hmac_secret="h" * 32)
    rate_limiter = RateLimitService(repo, "r" * 32)
    hasher = FakePasswordHasher()
    email_delivery = FakeEmailDelivery()

    auth_service = AccountAuthService(
        repository=repo,
        password_hasher=hasher,
        token_service=tokens,
        email_delivery=email_delivery,
        rate_limiter=rate_limiter,
        public_base_url="https://leads.example.com",
        google_verifier=verifier,
    )

    services = ProductServices(
        auth_service=auth_service,
        account_repository=repo,
        token_service=tokens,
        rate_limiter=rate_limiter,
    )

    settings = ApiSettings(
        api_key="a" * 32,
        docs_enabled=False,
        cors_origins=("https://leads.example.com",),
    )

    return create_app(
        settings,
        job_service=object(),
        job_repository=object(),
        user_repository=object(),
        readiness=lambda _migration: True,
        product_services=services,
        clock=lambda: NOW,
    )


def test_google_login_new_user_creates_account_and_returns_tokens():
    repo = ProductRepositoryFake()
    verifier = FakeGoogleVerifier(
        user_info=GoogleUserInfo(
            email="googleuser@example.com",
            email_verified=True,
            google_user_id="google-uid-12345",
            display_name="Google User",
        )
    )
    app = make_test_app(verifier, repo)
    client = TestClient(app)

    response = client.post(
        "/api/v1/auth/google",
        json={
            "id_token": "valid-google-id-token-xyz",
            "installation_id": str(INSTALLATION_ID),
            "device_name": "Chrome Extension",
            "transport": "extension",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_google_login_existing_user_authenticates():
    repo = ProductRepositoryFake()
    verifier = FakeGoogleVerifier(
        user_info=GoogleUserInfo(
            email="person@example.com",
            email_verified=True,
            google_user_id="google-uid-existing",
        )
    )
    app = make_test_app(verifier, repo)
    client = TestClient(app)

    response = client.post(
        "/api/v1/auth/google",
        json={
            "id_token": "valid-google-id-token-xyz",
            "installation_id": str(INSTALLATION_ID),
            "device_name": "Chrome Extension",
            "transport": "extension",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_google_login_invalid_token_fails():
    repo = ProductRepositoryFake()
    verifier = FakeGoogleVerifier(
        error=GoogleAuthError("Invalid Google ID token.")
    )
    app = make_test_app(verifier, repo)
    client = TestClient(app)

    response = client.post(
        "/api/v1/auth/google",
        json={
            "id_token": "invalid-token-here",
            "installation_id": str(INSTALLATION_ID),
            "device_name": "Chrome Extension",
            "transport": "extension",
        },
    )

    assert response.status_code in (400, 422)


def test_google_login_unverified_email_fails():
    repo = ProductRepositoryFake()
    verifier = FakeGoogleVerifier(
        user_info=GoogleUserInfo(
            email="unverified@example.com",
            email_verified=False,
            google_user_id="google-uid-unverified",
        )
    )
    app = make_test_app(verifier, repo)
    client = TestClient(app)

    response = client.post(
        "/api/v1/auth/google",
        json={
            "id_token": "unverified-email-token",
            "installation_id": str(INSTALLATION_ID),
            "device_name": "Chrome Extension",
            "transport": "extension",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "email_verification_required"
