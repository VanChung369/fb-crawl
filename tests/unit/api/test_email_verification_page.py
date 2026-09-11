from fastapi.testclient import TestClient

from fb_crawl.api.app import create_app
from fb_crawl.api.config import ApiSettings
from fb_crawl.composition.product import ProductServices
from tests.unit.auth.test_service import EMAIL, NOW, PASSWORD, service_parts
from urllib.parse import parse_qs, urlsplit


def test_email_verification_link_has_public_private_landing_page():
    app = create_app(
        ApiSettings(api_key="k" * 32),
        job_service=object(), job_repository=object(), user_repository=object(),
        readiness=lambda _migration: True,
    )
    response = TestClient(app).get("/verify-email?token=synthetic-test-token")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "no-store" in response.headers["cache-control"]
    assert "synthetic-test-token" not in response.text
    assert "Lead Finder" in response.text
    assert 'type="email"' in response.text
    assert "/api/v1/auth/verify-email" in response.text
    assert "/api/v1/auth/resend-verification" in response.text


def test_generated_verification_link_and_post_activate_account_once(service_parts):
    service, repository, email, _limiter, tokens = service_parts
    service.register(EMAIL, PASSWORD, NOW, ip_address="127.0.0.1")
    link = urlsplit(email.verifications[0][1])
    token = parse_qs(link.query)["token"][0]
    app = create_app(
        ApiSettings(api_key="k" * 32),
        job_service=object(), job_repository=object(), user_repository=object(),
        readiness=lambda _migration: True,
        product_services=ProductServices(service, repository, tokens),
        clock=lambda: NOW,
    )
    client = TestClient(app)
    assert client.get(f"{link.path}?{link.query}").status_code == 200
    assert repository.account.email_verified_at is None
    response = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert response.status_code == 200
    assert repository.account.email_verified_at == NOW
    reused = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert reused.status_code == 400
    assert token not in reused.text
