from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from fb_crawl.api.app import create_app
from fb_crawl.api.config import ApiSettings
from fb_crawl.core.jobs import Page

API_KEY = "a" * 32


@pytest.fixture
def api_client():
    settings = ApiSettings(
        api_key=API_KEY,
        host="127.0.0.1",
        port=8000,
        cors_origins=(),
        docs_enabled=True,
    )
    job_service = MagicMock()
    job_repository = MagicMock()
    user_repository = MagicMock()
    user_repository.database_url = "postgresql://mock"
    user_repository.list_users.return_value = Page((), None)
    readiness = MagicMock(return_value=True)

    app = create_app(
        settings=settings,
        job_service=job_service,
        job_repository=job_repository,
        user_repository=user_repository,
        readiness=readiness,
    )
    return TestClient(app)


def test_sync_fbnumber_scans_preview(api_client):
    mock_scans_response = {
        "status": "success",
        "data": [
            {
                "_id": "6a8da66491c69f3812eab32b",
                "birthday": "09/24",
                "gender": "Nữ",
                "linkFb": "https://fb.com/100003795620679",
                "location": "Ho Chi Minh City, Vietnam",
                "name": "Hồ Ngọc My Linh",
                "number": "0367131685",
                "number2": "0829462581",
                "number2Provider": "VinaPhone",
                "numberOfScans": 1,
                "numberProvider": "Viettel",
                "scanAt": "2026-08-25T14:27:48.166Z",
            }
        ],
        "totalCount": 46,
    }

    with patch("fb_data_pipeline.importers.fbnumber_scans.fetch_fbnumber_scans", return_value=mock_scans_response):
        response = api_client.post(
            "/api/v1/users/sync-fbnumber-scans",
            headers={"X-API-Key": API_KEY},
            json={
                "page_number": 1,
                "page_size": 5,
                "api_token": "mock_jwt_token",
                "preview": True,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["total_count"] == 46
    assert data["fetched_count"] == 1
    assert data["preview"] is True
    assert len(data["items"]) == 1
    assert data["items"][0]["uid"] == "100003795620679"
    assert data["items"][0]["name"] == "Hồ Ngọc My Linh"
    assert data["items"][0]["phone_1"] == "+84367131685"
    assert data["items"][0]["phone_2"] == "+84829462581"
