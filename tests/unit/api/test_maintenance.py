from dataclasses import replace

import pytest
from unittest.mock import Mock

from fb_crawl.accounts.models import AccountRole
from fb_crawl.maintenance import MaintenanceStore
from fb_crawl.maintenance import MaintenanceError
from fb_crawl.contacts.service import ContactLookupService, ContactLookupRequest
from tests.unit.api.product_auth_fakes import NOW
from tests.unit.api.product_auth_fakes import INSTALLATION_ID
from tests.unit.api.test_product_auth_routes import product_client


@pytest.fixture
def setup(tmp_path):
    client, repository, _, access = product_client()
    client.app.state.maintenance_store = MaintenanceStore(tmp_path / "maintenance.json")
    headers = {"Authorization": f"Bearer {access}", "X-Installation-ID": str(INSTALLATION_ID)}
    return client, repository, headers


def test_only_admin_can_change_maintenance_and_settings_survive_restart(setup, tmp_path):
    client, repository, headers = setup
    payload = {"enabled": True, "message": "Đang nâng cấp. Vui lòng quay lại sau."}
    assert client.post("/api/v1/admin/maintenance", headers=headers, json=payload).status_code == 403
    repository.account = replace(repository.account, role=AccountRole.ADMIN)
    response = client.post("/api/v1/admin/maintenance", headers=headers, json=payload)
    assert response.status_code == 200
    client.app.state.maintenance_store = MaintenanceStore(tmp_path / "maintenance.json")
    status = client.get("/api/v1/app/maintenance")
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["message"] == payload["message"]
    assert status.headers["cache-control"] == "no-store"
    assert client.post("/api/v1/admin/maintenance", headers=headers, json={**payload, "enabled": False}).status_code == 200
    assert client.get("/api/v1/app/maintenance").json()["enabled"] is False


@pytest.mark.parametrize("path", [
    "/api/v1/contacts/lookup", "/api/v1/contacts/batch-lookup",
    "/api/v1/crawl-jobs", "/api/v1/interaction-sessions",
    "/api/v1/interaction-sessions/00000000-0000-0000-0000-000000000001/people/person/lookup",
])
def test_maintenance_blocks_work_before_provider_or_quota_is_used(setup, path):
    client, _, headers = setup
    client.app.state.maintenance_store.write(True, "Hệ thống đang bảo trì.")
    response = client.post(path, headers=headers, json={})
    assert response.status_code == 503
    assert response.json()["code"] == "maintenance"
    assert response.json()["message"] == "Hệ thống đang bảo trì."


def test_maintenance_keeps_authentication_and_existing_data_accessible(setup):
    client, _, headers = setup
    client.app.state.maintenance_store.write(True, "Hệ thống đang bảo trì.")
    assert client.get("/api/v1/account/me", headers=headers).status_code == 200
    assert client.get("/api/v1/app/version").status_code == 200
    assert client.get("/api/v1/history", headers=headers).status_code != 503
    assert client.post("/api/v1/exports", headers=headers, json={}).status_code != 503
    assert client.post("/api/v1/contacts/lookup/", headers=headers, json={}).status_code == 503


def test_blank_enabled_message_uses_default_and_oversized_message_is_rejected(setup):
    client, repository, headers = setup
    repository.account = replace(repository.account, role=AccountRole.ADMIN)
    response = client.post("/api/v1/admin/maintenance", headers=headers, json={"enabled": True, "message": "   "})
    assert response.status_code == 200
    assert response.json()["message"].strip()
    assert client.post("/api/v1/admin/maintenance", headers=headers, json={"enabled": True, "message": "x" * 1001}).status_code == 400


def test_worker_phone_lookup_stops_before_accessing_provider_or_quota(tmp_path):
    store = MaintenanceStore(tmp_path / "maintenance.json")
    store.write(True, "Đang bảo trì.")
    quota, pipeline = Mock(), Mock()
    service = ContactLookupService(Mock(), quota, Mock(), pipeline, maintenance_store=store)
    with pytest.raises(MaintenanceError, match="Đang bảo trì"):
        service.lookup(Mock(), Mock(), ContactLookupRequest(facebook_uid="100000000000001"), NOW)
    assert not pipeline.mock_calls
    assert not quota.mock_calls


def test_stopping_and_saving_existing_session_remain_available_but_resume_is_blocked(tmp_path):
    from tests.unit.api.test_interaction_session_routes import setup as session_setup
    client, _, headers = session_setup()
    client.app.state.maintenance_store = MaintenanceStore(tmp_path / "maintenance.json")
    client.app.state.maintenance_store.write(True, "Bảo trì.")
    path = "/api/v1/interaction-sessions/00000000-0000-0000-0000-000000000001"
    assert client.patch(path, headers=headers, json={"status":"running", "revision":1}).status_code == 503
    assert client.patch(path, headers=headers, json={"status":"stopped", "revision":1}).status_code == 404
    assert client.put(path + "/interactions", headers=headers, json={}).status_code != 503
