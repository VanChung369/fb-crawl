from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from fb_crawl.core.jobs import Page
from fb_crawl.interaction_sessions.models import SessionCounters, SessionError, SessionSummary
from tests.unit.api.test_product_auth_routes import product_client
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from fb_crawl.accounts.models import DeviceStatus

ROOT = "/api/v1/interaction-sessions"
SID = uuid4()


class Sessions:
    def __init__(self):
        self.calls = []
    def create(self, account_id, value, now):
        self.calls.append((account_id, value))
        return SessionSummary(SID, value.client_session_id, value.source_url, value.kind, 1, "running", NOW, NOW, SessionCounters())
    def get(self, account_id, session_id):
        raise SessionError("interaction_session_not_found", 404)
    def list_sessions(self, account_id, filters, cursor, limit):
        self.calls.append((account_id, filters))
        return Page((), None)
    def upsert_rows(self, account_id, session_id, rows, now):
        self.calls.append((account_id, rows))
        raise SessionError("interaction_session_not_found", 404)
    def transition(self, *args):
        raise SessionError("interaction_session_not_found", 404)
    def delete(self, *args):
        raise SessionError("interaction_session_not_found", 404)
    def list_rows(self, *args):
        raise SessionError("interaction_session_not_found", 404)


def setup():
    service = Sessions()
    client, repo, _, access = product_client(interaction_session_service=service)
    return client, service, {"Authorization": f"Bearer {access}", "X-Installation-ID": str(INSTALLATION_ID)}


def test_create_normalizes_source_and_uses_bearer_account_not_internal_key():
    client, service, headers = setup()
    response = client.post(ROOT, headers=headers, json={"client_session_id": str(uuid4()), "source_url": "https://www.facebook.com/sample.user/posts/123456?locale=vi_VN", "kind": "comments"})
    assert response.status_code == 200
    assert response.json()["source_url"] == "https://www.facebook.com/sample.user/posts/123456"
    assert response.json()["counters"]["interactions"] == 0
    assert service.calls[0][0] == 7


def test_create_friend_session_uses_profile_friends_source():
    client, service, headers = setup()
    response = client.post(ROOT, headers=headers, json={
        'client_session_id': str(uuid4()),
        'source_url': 'https://www.facebook.com/tuan.anh.663583/friends?locale=vi_VN',
        'kind': 'friends',
    })
    assert response.status_code == 200
    assert response.json()['kind'] == 'friends'
    assert response.json()['source_url'] == 'https://www.facebook.com/tuan.anh.663583/friends'
    assert service.calls[0][0] == 7


def test_create_group_member_session():
    client, service, headers = setup()
    response = client.post(ROOT, headers=headers, json={
        'client_session_id': str(uuid4()),
        'source_url': 'https://www.facebook.com/groups/12345/members?locale=vi_VN',
        'kind': 'members',
    })
    assert response.status_code == 200
    assert response.json()['kind'] == 'members'
    assert service.calls[0][1].source_url == 'https://www.facebook.com/groups/12345/members'


@pytest.mark.parametrize("method,path,payload", [
    ("get", ROOT, None), ("post", ROOT, {}), ("get", f"{ROOT}/{SID}", None),
    ("put", f"{ROOT}/{SID}/interactions", {"rows": []}),
    ("patch", f"{ROOT}/{SID}", {"status": "stopped", "revision": 1}),
    ("delete", f"{ROOT}/{SID}", None), ("get", f"{ROOT}/{SID}/interactions", None),
])
def test_every_route_requires_product_auth(method, path, payload):
    client, service, _ = setup()
    response = client.request(method, path, json=payload)
    assert response.status_code == 401
    assert not service.calls


@pytest.mark.parametrize("extra", [{"account_id": 99}, {"phone": "0900000000"}, {"counters": {}}, {"lookup_event_id": 1}])
def test_rejects_forged_client_authority(extra):
    client, service, headers = setup()
    response = client.post(ROOT, headers=headers, json={"client_session_id": str(uuid4()), "source_url": "https://www.facebook.com/sample.user/posts/123456", "kind": "comments", **extra})
    assert response.status_code == 400
    assert not service.calls


def test_missing_session_is_safe_404_and_utf8_body_limit_is_enforced():
    client, _, headers = setup()
    response = client.get(f"{ROOT}/{SID}", headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "interaction_session_not_found"
    response = client.put(f"{ROOT}/{SID}/interactions", headers=headers, content='{"rows":[],"padding":"' + "界" * 350000 + '"}')
    assert response.status_code == 413
    assert client.get(ROOT + "?limit=101", headers=headers).status_code == 400


def test_revoked_or_foreign_device_cannot_read_sessions():
    service = Sessions()
    client, repo, _, access = product_client(interaction_session_service=service)
    headers = {"Authorization": f"Bearer {access}", "X-Installation-ID": str(INSTALLATION_ID)}
    repo.device = replace(repo.device, status=DeviceStatus.REVOKED)
    assert client.get(ROOT, headers=headers).status_code == 401
    assert not service.calls
    repo.device = replace(repo.device, status=DeviceStatus.ACTIVE)
    headers["X-Installation-ID"] = str(uuid4())
    assert client.get(ROOT, headers=headers).status_code == 401


def test_upload_rejects_unknown_fields_and_malformed_json_without_calling_service():
    client, service, headers = setup()
    for body in ('{"rows":[],"phone":"123"}', '{bad', '{"rows":[]}'):
        assert client.put(f"{ROOT}/{SID}/interactions", headers=headers, content=body).status_code == 400
    assert not service.calls


def test_lookup_payload_accepts_only_optional_numeric_resolved_uid():
    from fb_crawl.api.routes.interaction_sessions import LookupPayload
    from pydantic import ValidationError
    assert LookupPayload().resolved_uid is None
    assert LookupPayload(resolved_uid="100123").resolved_uid == "100123"
    for invalid in ("", "1234", "1" * 21, "sample.user", "12345\n", 100123):
        with pytest.raises(ValidationError):
            LookupPayload(resolved_uid=invalid)
    with pytest.raises(ValidationError):
        LookupPayload(resolved_uid="100123", username="other.person")


def test_lookup_route_forwards_resolved_uid_for_authenticated_person():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fb_crawl.api.routes.interaction_sessions import create_interaction_sessions_router
    from fb_crawl.contacts.models import LookupOutcome, LookupSource
    from fb_crawl.contacts.service import ContactLookupResult
    from tests.unit.contacts.test_service import ACCOUNT, DEVICE, CONTACT
    calls = []
    class Lookup:
        def lookup(self, account, device, session_id, person_id, now, retry_failed=False, resolved_uid=None):
            calls.append((account.id, session_id, person_id, resolved_uid, retry_failed))
            return ContactLookupResult(event_id=71, state=LookupOutcome.NOT_FOUND, source=LookupSource.PROVIDER, user=CONTACT.identity)
    current = SimpleNamespace(account=ACCOUNT, device=DEVICE, cookie_authenticated=False, device_allowed=True)
    app = FastAPI()
    app.include_router(create_interaction_sessions_router(Sessions(), lambda: current, lookup_service=Lookup(), clock=lambda: NOW))
    person_id = uuid4()
    response = TestClient(app).post(f"{ROOT}/{SID}/people/{person_id}/lookup", json={"resolved_uid": "100123"})
    assert response.status_code == 200
    assert calls == [(ACCOUNT.id, SID, person_id, "100123", False)]
