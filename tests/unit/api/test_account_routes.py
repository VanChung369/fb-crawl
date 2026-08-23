from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fb_crawl.core.jobs import AccountStatus, CrawlerAccountState, JobConflict, JobNotFound


API_KEY = "a" * 32
ROOT = Path(__file__).parents[3]
SCHEMAS_PATH = ROOT / "src" / "fb_crawl" / "api" / "schemas.py"
ACCOUNT_ROUTE_PATH = ROOT / "src" / "fb_crawl" / "api" / "routes" / "account.py"

try:
    import fastapi
    import pydantic
except ModuleNotFoundError as error:
    if error.name not in {"fastapi", "pydantic"}:
        raise
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True

if FASTAPI_AVAILABLE:
    from fastapi.testclient import TestClient

    from fb_crawl.api.app import create_app
    from fb_crawl.api.config import ApiSettings


def _account(**updates: object) -> CrawlerAccountState:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    state = CrawlerAccountState(
        account_key="default",
        status=AccountStatus.MANUAL_REVIEW,
        cooldown_until=datetime(2026, 8, 22, 18, tzinfo=UTC),
        last_job_id=None,
        last_started_at=now,
        last_finished_at=now,
        rate_limit_count_24h=2,
        last_rate_limit_at=now,
        last_warning_code="checkpoint",
        last_warning_at=now,
        block_reason="raw-internal-block-reason",
        acknowledged_at=None,
        created_at=now,
        updated_at=now,
    )
    return replace(state, **updates)


class AccountService:
    def __init__(self, account: CrawlerAccountState | None = None) -> None:
        self.account = account

    def get_account(self, account_key: str = "default") -> CrawlerAccountState:
        assert account_key == "default"
        if self.account is None:
            raise JobNotFound("Crawler account was not found.")
        return self.account

    def acknowledge_account(
        self, *, account_key: str = "default", acknowledged: bool,
    ) -> CrawlerAccountState:
        assert account_key == "default"
        if acknowledged is not True:
            raise JobConflict("Account acknowledgement must be true.")
        if self.account is None:
            raise JobNotFound("Crawler account was not found.")
        self.account = replace(
            self.account,
            status=AccountStatus.COOLDOWN,
            acknowledged_at=datetime(2026, 8, 22, 12, 30, tzinfo=UTC),
        )
        return self.account


def _client(service: AccountService):
    app = create_app(
        ApiSettings(api_key=API_KEY),
        job_service=service,
        job_repository=object(),
        user_repository=object(),
        readiness=lambda _migration: True,
    )
    return TestClient(app, raise_server_exceptions=False)


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def test_account_schema_and_route_are_closed_and_dependency_protected() -> None:
    """Break caught: account endpoints accept arbitrary recovery controls or expose internals."""

    schema_source = SCHEMAS_PATH.read_text(encoding="utf-8")
    route_source = ACCOUNT_ROUTE_PATH.read_text(encoding="utf-8")
    schema_tree = ast.parse(schema_source)
    classes = {
        node.name
        for node in schema_tree.body
        if isinstance(node, ast.ClassDef)
    }

    assert {"AccountAcknowledgeRequest", "AccountResponse"} <= classes
    assert 'ConfigDict(extra="forbid")' in schema_source
    assert "Literal[True]" in schema_source
    assert "Depends(auth)" in route_source
    assert "response_model=" in route_source
    assert "session" not in route_source.casefold()
    assert "proxy" not in route_source.casefold()
    assert "block_reason" not in schema_source
    assert "rate_limit_count_24h" not in schema_source


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_account_get_requires_auth_and_exposes_only_safe_state() -> None:
    """Break caught: the account read leaks session/proxy/internal recovery details."""

    client = _client(AccountService(_account()))

    denied = client.get("/api/v1/account/default")
    response = client.get("/api/v1/account/default", headers=_headers())

    assert denied.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "account_key": "default",
        "status": "manual_review",
        "cooldown_until": "2026-08-22T18:00:00Z",
        "last_started_at": "2026-08-22T12:00:00Z",
        "last_finished_at": "2026-08-22T12:00:00Z",
        "last_rate_limit_at": "2026-08-22T12:00:00Z",
        "last_warning_code": "checkpoint",
        "last_warning_at": "2026-08-22T12:00:00Z",
        "acknowledged_at": None,
        "created_at": "2026-08-22T12:00:00Z",
        "updated_at": "2026-08-22T12:00:00Z",
    }
    assert "session" not in response.text.casefold()
    assert "proxy" not in response.text.casefold()
    assert "raw-internal-block-reason" not in response.text


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_account_acknowledgement_requires_exact_true_and_returns_new_state() -> None:
    """Break caught: false or extra recovery controls clear an account hold."""

    service = AccountService(_account())
    client = _client(service)

    for body in (
        {"acknowledged": False},
        {},
        {"acknowledged": True, "session_path": "runtime/private-session.json"},
        {"acknowledged": True, "proxy": "http://private-proxy"},
        {"acknowledged": "true"},
    ):
        rejected = client.post(
            "/api/v1/account/default/acknowledge",
            headers=_headers(),
            json=body,
        )
        assert rejected.status_code == 400
        assert "private" not in rejected.text

    accepted = client.post(
        "/api/v1/account/default/acknowledge",
        headers=_headers(),
        json={"acknowledged": True},
    )

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "cooldown"
    assert accepted.json()["acknowledged_at"] == "2026-08-22T12:30:00Z"


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_missing_account_is_404_with_stable_safe_json() -> None:
    """Break caught: a missing default account becomes a generic 400 or 500 response."""

    client = _client(AccountService(None))

    response = client.get("/api/v1/account/default", headers=_headers())

    assert response.status_code == 404
    assert response.json() == {
        "code": "job_not_found",
        "message": "Crawler account was not found.",
    }


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_only_the_default_account_key_is_routable() -> None:
    """Break caught: callers can select an unapproved crawler account."""

    client = _client(AccountService(_account()))

    assert (
        client.get("/api/v1/account/other", headers=_headers()).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/account/other/acknowledge",
            headers=_headers(),
            json={"acknowledged": True},
        ).status_code
        == 404
    )
