import hmac

import pytest


try:
    import fastapi
    import pydantic
except ModuleNotFoundError as error:
    if error.name not in {"fastapi", "pydantic"}:
        raise
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True

pytestmark = pytest.mark.skipif(
    not FASTAPI_AVAILABLE,
    reason="optional api extra is not installed",
)

if FASTAPI_AVAILABLE:
    from fastapi.testclient import TestClient

    from fb_crawl.api.app import create_app
    from fb_crawl.api.config import ApiSettings
    from fb_crawl.api.dependencies import ApiAuthenticationError, ApiKeyAuth
    from fb_crawl.core.exceptions import FbCrawlError


API_KEY = "a" * 32
AUTH_ERROR = {
    "code": "api_unauthorized",
    "message": "API authentication failed.",
}


def _app(*, docs_enabled: bool = False):
    app = create_app(
        ApiSettings(api_key=API_KEY, docs_enabled=docs_enabled),
        job_service=object(),
        job_repository=object(),
        user_repository=object(),
        readiness=lambda required: True,
    )

    @app.get("/api/v1/protected-probe")
    def protected_probe() -> dict[str, str]:
        return {"status": "protected"}

    return app


def test_api_routes_reject_missing_and_wrong_keys_with_stable_json() -> None:
    """Break caught: a new API route becomes reachable without the shared key."""

    client = TestClient(_app())

    missing = client.get("/api/v1/protected-probe")
    wrong = client.get(
        "/api/v1/protected-probe",
        headers={"X-API-Key": "z" * 32},
    )
    correct = client.get(
        "/api/v1/protected-probe",
        headers={"X-API-Key": API_KEY},
    )

    assert missing.status_code == 401
    assert missing.json() == AUTH_ERROR
    assert wrong.status_code == 401
    assert wrong.json() == AUTH_ERROR
    assert correct.status_code == 200
    assert correct.json() == {"status": "protected"}


def test_api_key_comparison_uses_constant_time_primitive(monkeypatch) -> None:
    """Break caught: authentication regresses to an ordinary equality check."""

    calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def compare_digest(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr("fb_crawl.api.dependencies.hmac.compare_digest", compare_digest)

    response = TestClient(_app()).get(
        "/api/v1/protected-probe",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    assert calls == [(API_KEY.encode("utf-8"), API_KEY.encode("utf-8"))]


def test_unicode_api_key_comparison_is_total_for_correct_wrong_and_missing() -> None:
    """Break caught: Unicode configuration crashes auth instead of denying safely."""

    unicode_key = "khóa-bảo-mật-rất-dài-" * 3
    settings = ApiSettings(api_key=unicode_key)
    auth = ApiKeyAuth(settings.api_key)

    assert unicode_key not in repr(settings)
    auth.verify(unicode_key)
    for candidate in ("khóa-bảo-mật-khác-" * 3, None):
        with pytest.raises(ApiAuthenticationError) as raised:
            auth.verify(candidate)

        assert raised.value.code == "api_unauthorized"
        assert raised.value.safe_message == "API authentication failed."


def test_enabled_docs_and_schema_require_the_same_api_key() -> None:
    """Break caught: enabling OpenAPI exposes schema or interactive docs publicly."""

    client = TestClient(_app(docs_enabled=True))

    for path in ("/docs", "/openapi.json"):
        denied = client.get(path)
        allowed = client.get(path, headers={"X-API-Key": API_KEY})

        assert denied.status_code == 401
        assert denied.json() == AUTH_ERROR
        assert allowed.status_code == 200


def test_docs_and_schema_are_absent_by_default() -> None:
    """Break caught: API metadata is exposed despite docs being disabled."""

    client = TestClient(_app())

    assert client.get("/docs", headers={"X-API-Key": API_KEY}).status_code == 404
    assert (
        client.get("/openapi.json", headers={"X-API-Key": API_KEY}).status_code
        == 404
    )


def test_domain_and_unexpected_errors_have_safe_stable_responses() -> None:
    """Break caught: exception text or configured secrets escape in JSON."""

    app = _app()

    @app.get("/api/v1/domain-error")
    def domain_error() -> None:
        raise FbCrawlError("Safe domain message.")

    @app.get("/api/v1/internal-error")
    def internal_error() -> None:
        raise RuntimeError(
            f"database=postgresql://private.example/secret key={API_KEY}"
        )

    client = TestClient(app, raise_server_exceptions=False)

    domain = client.get("/api/v1/domain-error", headers={"X-API-Key": API_KEY})
    internal = client.get("/api/v1/internal-error", headers={"X-API-Key": API_KEY})

    assert domain.status_code == 500
    assert domain.json() == {
        "code": "fb_crawl_error",
        "message": "Safe domain message.",
    }
    assert internal.status_code == 500
    assert internal.json() == {
        "code": "internal_server_error",
        "message": "Internal server error.",
    }
    assert API_KEY not in internal.text
    assert "postgresql" not in internal.text
