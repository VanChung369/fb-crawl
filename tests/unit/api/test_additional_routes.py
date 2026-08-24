from __future__ import annotations

from pathlib import Path
import ast
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from fb_crawl.api.app import create_app
from fb_crawl.api.config import ApiSettings
from fb_crawl.adapters.browser.session_pool import SessionPool
from fb_crawl.core.jobs import Page
from fb_crawl.core.proxy_pool import ProxyPool
from fb_data_pipeline.repositories.users import UserSummary

API_KEY = "k" * 32
HEADERS = {"X-API-Key": API_KEY}
ROOT = Path(__file__).parents[3]
SESSIONS_ROUTE_PATH = ROOT / "src" / "fb_crawl" / "api" / "routes" / "sessions.py"
SCHEMAS_PATH = ROOT / "src" / "fb_crawl" / "api" / "schemas.py"


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


def test_proxy_list_masks_credentials(tmp_path: Path) -> None:
    """Break caught: proxy usernames/passwords leak through the dashboard API."""

    proxy_pool = ProxyPool(["http://proxy-user:proxy-secret@127.0.0.1:8080"])
    app = create_app(
        ApiSettings(api_key=API_KEY),
        job_service=MagicMock(),
        job_repository=MagicMock(),
        user_repository=MagicMock(),
        readiness=lambda m: True,
        proxy_pool=proxy_pool,
        session_pool=SessionPool(sessions_dir=tmp_path / "sessions", proxy_pool=proxy_pool),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)

    response = client.get("/api/v1/proxies", headers=HEADERS)

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["display_url"] == "http://127.0.0.1:8080"
    assert "raw_url" not in item
    assert "proxy-user" not in response.text
    assert "proxy-secret" not in response.text


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


def test_sessions_api_lists_unchecked_cookie_files_as_unknown(
    tmp_path: Path,
) -> None:
    """Break caught: dashboard reload paints unmanaged session files green."""

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "account_01.json").write_text(
        '[{"name": "c_user", "value": "1000123"}]',
        encoding="utf-8",
    )
    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/sessions", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["available_count"] == 0
    assert body["items"][0]["status"] == "unknown"
    assert body["items"][0]["is_available"] is False


def test_session_import_rejects_path_traversal_name(tmp_path: Path) -> None:
    """Break caught: a submitted session name can write outside sessions_dir."""

    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/v1/sessions",
        headers=HEADERS,
        json={
            "name": "../outside",
            "cookies": [{"name": "c_user", "value": "1000123"}],
        },
    )

    assert response.status_code == 400
    assert not (tmp_path / "outside.json").exists()


def test_session_api_no_longer_accepts_facebook_password_payloads() -> None:
    """Break caught: WebUI/API can receive Facebook password or 2FA secrets."""

    schema_source = SCHEMAS_PATH.read_text(encoding="utf-8")
    route_source = SESSIONS_ROUTE_PATH.read_text(encoding="utf-8")
    schema_tree = ast.parse(schema_source)
    route_tree = ast.parse(route_source)

    schema_classes = {
        node.name
        for node in schema_tree.body
        if isinstance(node, ast.ClassDef)
    }
    route_paths = [
        decorator.args[0].value
        for node in ast.walk(route_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        )
    ]

    assert "SessionExtractRequest" not in schema_classes
    assert "/extract" not in route_paths
    assert "password" not in schema_source
    assert "two_factor" not in schema_source
    assert "extract_session_from_credentials" not in route_source


def test_session_launch_login_is_local_only(tmp_path: Path) -> None:
    """Break caught: a LAN/public API can start a local browser with session cookies."""

    proxy_pool = ProxyPool([])
    session_pool = SessionPool(sessions_dir=tmp_path / "sessions", proxy_pool=proxy_pool)
    app = create_app(
        ApiSettings(api_key=API_KEY, host="0.0.0.0"),
        job_service=MagicMock(),
        job_repository=MagicMock(),
        user_repository=MagicMock(),
        readiness=lambda m: True,
        proxy_pool=proxy_pool,
        session_pool=session_pool,
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/sessions/account_01/launch-login",
        headers=HEADERS,
    )

    assert response.status_code == 403


def test_stats_overview_api(tmp_path: Path) -> None:
    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    res = client.get("/api/v1/stats/overview", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert "total_users" in data
    assert "total_proxies" in data
    assert data["total_proxies"] == 1


def test_fbnumber_settings_response_returns_configured_token_for_local_dashboard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Break caught: local settings UI cannot see/edit the configured JWT."""

    monkeypatch.setenv("FB_NUMBER_API_URL", "https://api.example.test/search")
    monkeypatch.setenv("FB_NUMBER_API_TOKEN", "fbnumber-private-token")

    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/settings/fbnumber", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["api_url"] == "https://api.example.test/search"
    assert body["is_configured"] is True
    assert body["api_token_configured"] is True
    assert body["api_token"] == "fbnumber-private-token"


def test_fbnumber_settings_update_preserves_existing_token_when_ui_keeps_mask(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Break caught: saving settings with a masked token overwrites the real JWT."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FB_NUMBER_API_URL", "https://api.example.test/search")
    monkeypatch.setenv("FB_NUMBER_API_TOKEN", "fbnumber-private-token")

    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/v1/settings/fbnumber",
        headers=HEADERS,
        json={
            "api_url": "https://api2.example.test/search",
            "api_token": None,
            "timeout_seconds": 20,
            "max_retries": 3,
            "default_country_code": "84",
        },
    )

    assert response.status_code == 200
    assert response.json()["api_token"] == "fbnumber-private-token"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "FB_NUMBER_API_URL=https://api2.example.test/search" in env_text
    assert "FB_NUMBER_API_TOKEN=fbnumber-private-token" in env_text
    assert "FB_NUMBER_API_TOKEN=********" not in env_text


def test_fbnumber_settings_falls_back_to_dotenv_when_process_env_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Break caught: saving settings loses JWT when the API process has not loaded .env."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FB_NUMBER_API_URL", raising=False)
    monkeypatch.delenv("FB_NUMBER_API_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "FB_NUMBER_API_URL=https://dotenv.example.test/search",
                "FB_NUMBER_API_TOKEN=fbnumber-private-token",
                "FB_NUMBER_TIMEOUT_SECONDS=11",
                "FB_NUMBER_MAX_RETRIES=1",
                "PIPELINE_DEFAULT_COUNTRY_CODE=84",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    loaded = client.get("/api/v1/settings/fbnumber", headers=HEADERS)
    saved = client.post(
        "/api/v1/settings/fbnumber",
        headers=HEADERS,
        json={
            "api_url": "https://dotenv2.example.test/search",
            "api_token": None,
            "timeout_seconds": 12,
            "max_retries": 2,
            "default_country_code": "84",
        },
    )

    assert loaded.status_code == 200
    assert loaded.json()["api_url"] == "https://dotenv.example.test/search"
    assert loaded.json()["api_token"] == "fbnumber-private-token"
    assert saved.status_code == 200
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "FB_NUMBER_API_URL=https://dotenv2.example.test/search" in env_text
    assert "FB_NUMBER_API_TOKEN=fbnumber-private-token" in env_text


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


def test_worker_settings_and_reset_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    app, _, _, _ = _create_test_app(tmp_path)
    client = TestClient(app)

    get_res = client.get("/api/v1/settings/worker", headers=HEADERS)
    assert get_res.status_code == 200
    data = get_res.json()
    assert "cooldown_seconds" in data
    assert "navigation_delay_seconds" in data
    assert "account_status" in data

    post_res = client.post(
        "/api/v1/settings/worker",
        headers=HEADERS,
        json={
            "cooldown_seconds": 60,
            "navigation_delay_seconds": 10,
            "job_timeout_seconds": 1200,
            "rate_limit_cooldown_seconds": 3600,
        },
    )
    assert post_res.status_code == 200
    updated = post_res.json()
    assert updated["cooldown_seconds"] == 60
    assert updated["navigation_delay_seconds"] == 10
    assert updated["job_timeout_seconds"] == 1200

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CRAWL_WORKER_COOLDOWN_SECONDS=60" in env_text

    reset_res = client.post("/api/v1/settings/reset-cooldown", headers=HEADERS)
    assert reset_res.status_code == 200
    assert reset_res.json()["status"] == "success"
