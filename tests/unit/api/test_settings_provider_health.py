from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.routes.settings import create_settings_router
from fb_crawl.providers.health import ProviderHealth


NOW = datetime(2026, 9, 2, 2, tzinfo=UTC)


class Repository:
    def get(self, provider_name):
        assert provider_name == "fbnumber"
        return ProviderHealth("fbnumber", True, NOW, "", NOW)


def test_admin_provider_health_returns_safe_shared_status_only() -> None:
    app = FastAPI()
    app.include_router(create_settings_router(
        auth=ApiKeyAuth("a" * 32),
        provider_health_repository=Repository(),
    ))
    client = TestClient(app)

    response = client.get(
        "/api/v1/settings/fbnumber/health",
        headers={"X-API-Key": "a" * 32},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "fbnumber",
        "configured": True,
        "last_success_at": "2026-09-02T02:00:00Z",
        "safe_error_code": "",
        "updated_at": "2026-09-02T02:00:00Z",
    }
    assert "token" not in response.text.casefold()
