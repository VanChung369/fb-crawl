import io
import json
import zipfile
from dataclasses import replace

import pytest

from fb_crawl.accounts.models import AccountRole
from tests.unit.api.product_auth_fakes import INSTALLATION_ID
from tests.unit.api.test_product_auth_routes import product_client


def archive(version="0.3.0", name="manifest.json"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr(zipfile.ZipInfo(name), json.dumps({"manifest_version": 3, "version": version, "name": "Lead Finder"}))
        z.writestr(zipfile.ZipInfo("background.js"), "// extension")
    return output.getvalue()


@pytest.fixture
def setup(tmp_path):
    from fb_crawl.releases import MemoryReleaseRepository, ReleaseService
    client, repo, _, access = product_client()
    client.app.state.release_service = ReleaseService(MemoryReleaseRepository(), tmp_path)
    headers = {"Authorization": f"Bearer {access}", "X-Installation-ID": str(INSTALLATION_ID)}
    return client, repo, headers


def upload(client, headers, version="0.3.0", data=None):
    return client.post("/api/v1/admin/releases", headers={**headers, "Content-Type": "application/zip", "X-Release-Version": version}, content=archive(version) if data is None else data)


def test_admin_upload_activate_download_and_delete(setup):
    client, repo, headers = setup
    assert upload(client, headers).status_code == 403
    repo.account = replace(repo.account, role=AccountRole.ADMIN)
    result = upload(client, headers)
    assert result.status_code == 201
    release = result.json()
    assert client.get("/api/v1/app/version").json()["announcement_enabled"] is False
    policy = {"active_release_id": release["id"], "announcement_enabled": True, "enforcement_enabled": True, "min_supported_version": "0.2.5", "message": "Cập nhật {version}"}
    assert client.put("/api/v1/admin/releases/policy", headers=headers, json=policy).status_code == 200
    public = client.get("/api/v1/app/version")
    assert public.json()["latest_version"] == "0.3.0"
    assert public.json()["release_notes"] == "Cập nhật 0.3.0"
    assert public.headers["cache-control"] == "no-store"
    assert client.get(public.json()["download_url"]).content == archive()
    assert client.delete(f'/api/v1/admin/releases/{release["id"]}', headers=headers).status_code == 409
    policy.update(active_release_id=None, announcement_enabled=False, enforcement_enabled=False)
    assert client.put("/api/v1/admin/releases/policy", headers=headers, json=policy).status_code == 200
    assert client.delete(f'/api/v1/admin/releases/{release["id"]}', headers=headers).status_code == 204
    assert client.get(public.json()["download_url"]).status_code == 404


@pytest.mark.parametrize("data", [b"not a zip", archive("0.2.0"), archive(name="../manifest.json")], ids=['invalid','mismatch','unsafe-path'])
def test_invalid_zip_is_rejected_without_artifacts(setup, data):
    client, repo, headers = setup
    repo.account = replace(repo.account, role=AccountRole.ADMIN)
    assert upload(client, headers, data=data).status_code == 400
    assert client.get("/api/v1/admin/releases", headers=headers).json()["releases"] == []


def test_enforcement_blocks_old_and_unknown_versions_but_keeps_data_available(setup):
    client, repo, headers = setup
    repo.account = replace(repo.account, role=AccountRole.ADMIN)
    release = upload(client, headers).json()
    policy = {"active_release_id": release["id"], "announcement_enabled": False, "enforcement_enabled": True, "min_supported_version": "0.2.5", "message": "Cần cập nhật"}
    assert client.put("/api/v1/admin/releases/policy", headers=headers, json=policy).status_code == 200
    for version in (None, "garbage", "0.2.4"):
        request_headers = headers if version is None else {**headers, "X-Extension-Version": version}
        response = client.post("/api/v1/contacts/lookup", headers=request_headers, json={})
        assert response.status_code == 426
        assert response.json()["code"] == "extension_update_required"
    for version in ("0.2.5", "0.2.10", "0.3.0"):
        assert client.post("/api/v1/contacts/lookup", headers={**headers, "X-Extension-Version": version}, json={}).status_code != 426
    assert client.get("/api/v1/history", headers=headers).status_code != 426
    assert client.post("/api/v1/exports", headers=headers, json={}).status_code != 426
    assert client.get("/api/v1/account/me", headers=headers).status_code == 200
    policy["min_supported_version"] = "0.4.0"
    assert client.put("/api/v1/admin/releases/policy", headers=headers, json=policy).status_code == 400


def test_duplicate_version_and_invalid_policy_are_rejected(setup):
    client, repo, headers = setup
    repo.account = replace(repo.account, role=AccountRole.ADMIN)
    assert upload(client, headers).status_code == 201
    assert upload(client, headers).status_code == 409
    assert client.put("/api/v1/admin/releases/policy", headers=headers, json={"active_release_id": None, "announcement_enabled": True, "enforcement_enabled": False, "min_supported_version": "0.2.0", "message": ""}).status_code == 400


def test_non_admin_cannot_list_change_or_delete_releases(setup):
    client, _, headers = setup
    assert client.get('/api/v1/admin/releases',headers=headers).status_code == 403
    assert client.put('/api/v1/admin/releases/policy',headers=headers,json={}).status_code == 403
    assert client.delete('/api/v1/admin/releases/00000000-0000-0000-0000-000000000001',headers=headers).status_code == 403


@pytest.mark.parametrize('enabled',[False,True])
def test_admin_can_explicitly_unpublish_and_delete_active_release(setup,enabled):
    client, repo, headers = setup
    repo.account = replace(repo.account,role=AccountRole.ADMIN)
    release=upload(client,headers).json()
    policy={'active_release_id':release['id'],'announcement_enabled':enabled,'enforcement_enabled':enabled,'min_supported_version':'0.2.5','message':'Update'}
    assert client.put('/api/v1/admin/releases/policy',headers=headers,json=policy).status_code==200
    deleted=client.delete(f"/api/v1/admin/releases/{release['id']}?unpublish=true",headers=headers)
    assert deleted.status_code==204
    value=client.get('/api/v1/admin/releases',headers=headers).json()
    assert value['releases']==[]
    assert value['policy']['active_release_id'] is None
    assert value['policy']['announcement_enabled'] is False
    assert value['policy']['enforcement_enabled'] is False
    assert value['policy']['min_supported_version']=='0.0.0'
    assert client.get(f"/api/v1/app/releases/{release['id']}/download").status_code==404


def test_deleting_draft_with_unpublish_does_not_clear_another_active_release(setup):
    client, repo, headers = setup
    repo.account = replace(repo.account,role=AccountRole.ADMIN)
    first=upload(client,headers).json()
    draft=upload(client,headers,'0.4.0').json()
    policy={'active_release_id':first['id'],'announcement_enabled':True,'enforcement_enabled':True,'min_supported_version':'0.2.5','message':'Update'}
    assert client.put('/api/v1/admin/releases/policy',headers=headers,json=policy).status_code==200
    assert client.delete(f"/api/v1/admin/releases/{draft['id']}?unpublish=true",headers=headers).status_code==204
    assert client.get('/api/v1/admin/releases',headers=headers).json()['policy']==policy


def test_old_version_cannot_resume_but_can_stop_session(tmp_path):
    from tests.unit.api.test_interaction_session_routes import setup as session_setup
    from fb_crawl.releases import ReleaseService, MemoryReleaseRepository
    client, _, headers = session_setup()
    client.app.state.release_service = ReleaseService(MemoryReleaseRepository(), tmp_path)
    client.app.state.release_service.repository.policy.enforcement_enabled = True
    client.app.state.release_service.repository.policy.min_supported_version = '0.2.5'
    path = '/api/v1/interaction-sessions/00000000-0000-0000-0000-000000000001'
    assert client.patch(path,headers={**headers,'X-Extension-Version':'0.2.0'},json={'status':'running','revision':1}).status_code == 426
    assert client.patch(path,headers=headers,json={'status':'stopped','revision':1}).status_code == 404
