from __future__ import annotations

from dataclasses import replace

from fb_crawl.accounts.models import AccountRole
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_license_routes import (
    EntitlementServiceFake,
    LicenseServiceFake,
)
from tests.unit.api.test_product_auth_routes import product_client
from fb_crawl.entitlements.models import Entitlements


class AdminLicenseServiceFake(LicenseServiceFake):
    def __init__(self) -> None:
        super().__init__()
        self.created = []
        self.audits = []

    def create_key(self, grant, actor_account_id: int, now):
        self.created.append((grant, actor_account_id))
        return self.key, "LF-REAL-PLAINTEXT-ABCD"

    def list_keys(self, *, limit: int = 100, cursor: int | None = None):
        return (self.key,)

    def revoke_key(self, key_id: int, actor_account_id: int, now):
        return replace(self.key, status=self.key.status.REVOKED, revoked_at=now)

    def list_subscriptions(self, account_id: int):
        return (self.subscription,)

    def start_subscription_now(
        self, account_id: int, subscription_id: int, actor_account_id: int, now
    ):
        return (self.subscription,)

    def write_audit(self, **event):
        self.audits.append(event)


def _headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


GRANT = {
    "duration": {"unit": "day", "value": 7},
    "monthly_contact_limit": 500,
    "max_devices": 2,
    "allow_group_crawl": True,
    "allow_comment_crawl": False,
}


def _client(*, admin: bool):
    licenses = AdminLicenseServiceFake()
    client, repository, _auth, access = product_client(
        license_service=licenses,
        entitlement_service=EntitlementServiceFake(
            Entitlements(500, 2, True, False)
        ),
    )
    if admin:
        repository.account = replace(repository.account, role=AccountRole.ADMIN)
    return client, repository, licenses, access


def test_user_cannot_create_license() -> None:
    client, _repository, _licenses, access = _client(admin=False)

    response = client.post(
        "/api/v1/admin/license-keys", headers=_headers(access), json=GRANT
    )

    assert response.status_code == 403


def test_created_key_plaintext_is_not_returned_by_list() -> None:
    client, _repository, _licenses, access = _client(admin=True)

    created = client.post(
        "/api/v1/admin/license-keys", headers=_headers(access), json=GRANT
    )
    listed = client.get(
        "/api/v1/admin/license-keys", headers=_headers(access)
    )

    assert created.status_code == listed.status_code == 200
    assert created.json()["key"].startswith("LF-")
    assert listed.json()["items"][0]["masked_key"] == "LF-****-****-ABCD"
    assert listed.json()["next_cursor"] is None
    assert all("key" not in row for row in listed.json()["items"])
    assert "private-digest" not in created.text + listed.text


def test_admin_can_list_and_suspend_accounts() -> None:
    client, repository, licenses, access = _client(admin=True)

    listed = client.get("/api/v1/admin/accounts", headers=_headers(access))
    suspended = client.post(
        "/api/v1/admin/accounts/7/suspend", headers=_headers(access)
    )

    assert listed.status_code == suspended.status_code == 200
    assert listed.json()["items"][0]["email"] == "Person@example.com"
    assert suspended.json()["status"] == "suspended"
    assert repository.suspended_accounts == [7]
    assert licenses.audits[0]["action"] == "account_suspended"


def test_admin_routes_do_not_require_internal_crawler_api_key() -> None:
    client, _repository, _licenses, access = _client(admin=True)

    response = client.get(
        "/api/v1/admin/license-keys", headers=_headers(access)
    )

    assert response.status_code == 200
