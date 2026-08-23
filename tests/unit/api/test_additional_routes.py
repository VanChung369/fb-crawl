from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from fb_crawl.api.app import create_app
from fb_crawl.api.config import ApiSettings
from fb_crawl.adapters.browser.session_pool import SessionPool
from fb_crawl.core.jobs import Page
from fb_crawl.core.proxy_pool import ProxyPool
from fb_data_pipeline.repositories.users import UserSummary

API_KEY = "k" * 32
HEADERS = {"X-API-Key": API_KEY}


def _create_test_app(tmp_path: Path):
    proxy_pool = ProxyPool(["http://127.0.0.1:8080"])
    session_pool = SessionPool(sessions_dir=tmp_path / "sessions", proxy_pool=proxy_pool)

    user_repo = MagicMock()
    user_sample = UserSummary(
        id=1,
        facebook_uid="10001",
        username="user1",
        name="User One",
        profile_url="https://facebook.com/user1",
        phone_1="0912345678",
        phone_2=None,
        address="Hanoi",
        birth_date="1995-01-01",
        gender="male",
        created_at="2026-08-20T10:00:00Z",
        updated_at="2026-08-20T10:00:00Z",
    )
    user_repo.list_users.return_value = Page(items=[user_sample], next_cursor=None)

    job_repo = MagicMock()
    job_repo.list_jobs.return_value = Page(items=[], next_cursor=None)

    app = create_app(
        ApiSettings(api_key=API_KEY, docs_enabled=True),
        job_service=MagicMock(),
        job_repository=job_repo,
        user_repository=user_repo,
        readiness=lambda m: True,
        proxy_pool=proxy_pool,
        session_pool=session_pool,
        sessions_dir=tmp_path / "sessions",
    )
    return app, proxy_pool, session_pool, user_repo


def test_proxies_api_crud(tmp_path: Path) -> None:
    app, proxy_pool, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    # List proxies
    res = client.get("/api/v1/proxies", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert data["total_count"] == 1
    assert data["items"][0]["host"] == "127.0.0.1"

    # Add proxy
    add_res = client.post("/api/v1/proxies", headers=HEADERS, json={"proxies": ["http://10.0.0.1:3128"]})
    assert add_res.status_code == 201
    assert add_res.json()["total_count"] == 2


def test_sessions_api_crud(tmp_path: Path) -> None:
    app, _, session_pool, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    # List sessions initially empty
    res = client.get("/api/v1/sessions", headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["total_count"] == 0

    # Import new session
    import_res = client.post(
        "/api/v1/sessions",
        headers=HEADERS,
        json={
            "name": "account_01",
            "cookies": [{"name": "c_user", "value": "1000123"}],
            "proxy": "http://127.0.0.1:8080",
        },
    )
    assert import_res.status_code == 201
    assert import_res.json()["name"] == "account_01.json"
    assert import_res.json()["proxy"] == "http://127.0.0.1:8080"


def test_stats_overview_api(tmp_path: Path) -> None:
    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    res = client.get("/api/v1/stats/overview", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert "total_users" in data
    assert "total_proxies" in data
    assert data["total_proxies"] == 1


def test_export_users_csv_and_json(tmp_path: Path) -> None:
    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    # Export CSV
    csv_res = client.get("/api/v1/export/users?format=csv", headers=HEADERS)
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "User One" in csv_res.text
    assert "0912345678" in csv_res.text

    # Export JSON
    json_res = client.get("/api/v1/export/users?format=json", headers=HEADERS)
    assert json_res.status_code == 200
    assert "application/json" in json_res.headers["content-type"]
    assert len(json_json := json_res.json()) == 1
    assert json_json[0]["username"] == "user1"


def test_dashboard_static_page_served(tmp_path: Path) -> None:
    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    res = client.get("/dashboard")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "fb-crawl" in res.text
