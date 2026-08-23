import subprocess
import sys
import textwrap
from pathlib import Path

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
    from fb_crawl.api.routes.health import REQUIRED_MIGRATION


API_KEY = "h" * 32
NOT_READY = {"code": "api_not_ready", "message": "API is not ready."}


class ReadinessSpy:
    def __init__(self, result: bool = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    def __call__(self, required_migration: str) -> bool:
        self.calls.append(required_migration)
        if self.error is not None:
            raise self.error
        return self.result


def _app(readiness: ReadinessSpy):
    return create_app(
        ApiSettings(api_key=API_KEY),
        job_service=object(),
        job_repository=object(),
        user_repository=object(),
        readiness=readiness,
    )


def test_liveness_is_public_and_never_touches_database_readiness() -> None:
    """Break caught: a database outage makes process liveness fail or block."""

    readiness = ReadinessSpy(error=AssertionError("must not query database"))

    response = TestClient(_app(readiness)).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert readiness.calls == []


def test_readiness_is_public_and_requires_migration_003() -> None:
    """Break caught: API accepts traffic before the orchestration schema exists."""

    readiness = ReadinessSpy(result=True)

    response = TestClient(_app(readiness)).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert readiness.calls == ["003_job_orchestration"]
    assert REQUIRED_MIGRATION == "003_job_orchestration"


def test_readiness_failure_and_database_error_return_safe_503() -> None:
    """Break caught: failed readiness leaks a database URL or reports ready."""

    for readiness in (
        ReadinessSpy(result=False),
        ReadinessSpy(error=RuntimeError("postgresql://private.example/secret")),
    ):
        response = TestClient(_app(readiness)).get("/health/ready")

        assert response.status_code == 503
        assert response.json() == NOT_READY
        assert "postgresql" not in response.text


def test_cors_is_disabled_by_default_and_exact_when_configured() -> None:
    """Break caught: API grants browser access to an unconfigured origin."""

    default_client = TestClient(_app(ReadinessSpy()))
    configured_app = create_app(
        ApiSettings(
            api_key=API_KEY,
            cors_origins=("https://ui.example.test",),
        ),
        job_service=object(),
        job_repository=object(),
        user_repository=object(),
        readiness=ReadinessSpy(),
    )
    configured_client = TestClient(configured_app)

    default = default_client.options(
        "/api/v1/protected-probe",
        headers={
            "Origin": "https://ui.example.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    allowed = configured_client.options(
        "/api/v1/protected-probe",
        headers={
            "Origin": "https://ui.example.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = configured_client.options(
        "/api/v1/protected-probe",
        headers={
            "Origin": "https://evil.example.test",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in default.headers
    assert allowed.headers["access-control-allow-origin"] == "https://ui.example.test"
    assert "access-control-allow-origin" not in denied.headers


def test_api_foundation_import_does_not_load_browser_or_selenium() -> None:
    """Break caught: starting/importing the API takes ownership of a browser."""

    repository_root = Path(__file__).parents[3]
    probe = textwrap.dedent(
        """
        import builtins

        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            blocked = (
                name == "selenium"
                or name.startswith("selenium.")
                or name == "fb_crawl.adapters.browser"
                or name.startswith("fb_crawl.adapters.browser.")
            )
            if blocked:
                raise AssertionError(f"browser import attempted: {name}")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import

        from fb_crawl.api.app import create_app
        from fb_crawl.api.config import ApiSettings

        app = create_app(
            ApiSettings(api_key="p" * 32),
            job_service=object(),
            job_repository=object(),
            user_repository=object(),
            readiness=lambda required: True,
        )
        assert app.state.job_service is not None
        print("API_IMPORT_BUILD_NO_BROWSER_OK")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "API_IMPORT_BUILD_NO_BROWSER_OK"
