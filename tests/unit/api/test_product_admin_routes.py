from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from datetime import timedelta

from fb_crawl.accounts.models import AccountRole
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_license_routes import (
    EntitlementServiceFake,
    LicenseServiceFake,
)
from tests.unit.api.test_product_auth_routes import product_client
from fb_crawl.entitlements.models import Entitlements
from fb_crawl.licenses.models import AdminAuditEvent


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

    def reveal_key(self, key_id, actor_account_id, now):
        from fb_crawl.licenses.repository import InvalidLicenseKey, LicenseKeyRevealUnavailable
        if key_id == 999:
            raise InvalidLicenseKey("License key not found.")
        if key_id == 998:
            raise LicenseKeyRevealUnavailable("License key cannot be revealed.")
        return "LF-REAL-PLAINTEXT-ABCD"

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

    def list_audit_events(self, *, limit: int = 100, cursor: int | None = None):
        return (
            AdminAuditEvent(41, 7, "license_key_created", "license", "11", {}, NOW),
            AdminAuditEvent(40, 7, "account_suspended", "account", "8", {}, NOW),
        )


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


def test_only_admin_can_reveal_key_and_response_is_not_cached() -> None:
    for admin, expected in ((False, 403), (True, 200)):
        client, _, _, access = _client(admin=admin)
        response = client.post("/api/v1/admin/license-keys/11/reveal", headers=_headers(access))
        assert response.status_code == expected
        if admin:
            assert response.json() == {"key": "LF-REAL-PLAINTEXT-ABCD"}
            assert response.headers["cache-control"] == "no-store"


def test_reveal_returns_clear_errors_for_missing_and_legacy_keys() -> None:
    client, _, _, access = _client(admin=True)
    for key_id, status in ((999, 404), (998, 409)):
        response = client.post(f"/api/v1/admin/license-keys/{key_id}/reveal", headers=_headers(access))
        assert response.status_code == status


def test_list_exposes_reveal_availability_but_neither_ciphertext_nor_plaintext() -> None:
    client, _, licenses, access = _client(admin=True)
    licenses.key = replace(licenses.key, encrypted_key="private-ciphertext")
    response = client.get("/api/v1/admin/license-keys", headers=_headers(access))
    assert response.status_code == 200
    assert response.json()["items"][0]["can_reveal"] is True
    assert "private-ciphertext" not in response.text
    assert "LF-REAL-PLAINTEXT-ABCD" not in response.text


def test_unauthenticated_client_cannot_reveal_keys() -> None:
    client, _, _, _ = _client(admin=True)
    response = client.post("/api/v1/admin/license-keys/11/reveal")
    assert response.status_code == 401


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


def test_admin_can_create_unlimited_license_key() -> None:
    client, _repository, licenses, access = _client(admin=True)

    unlimited_grant = {
        "duration": {"unit": "month", "value": 1200},
        "monthly_contact_limit": 2_147_483_647,
        "max_devices": 1_000_000,
        "allow_group_crawl": True,
        "allow_comment_crawl": True,
    }
    response = client.post(
        "/api/v1/admin/license-keys",
        headers=_headers(access),
        json=unlimited_grant,
    )

    assert response.status_code == 200
    assert response.json()["key"] == "LF-REAL-PLAINTEXT-ABCD"
    assert len(licenses.created) == 1
    created_grant, _ = licenses.created[0]
    assert created_grant.duration.unit == "month"
    assert created_grant.duration.value == 1200
    assert created_grant.monthly_contact_limit == 2_147_483_647
    assert created_grant.max_devices == 1_000_000



def test_admin_can_list_and_suspend_accounts() -> None:
    client, repository, licenses, access = _client(admin=True)
    repository.add_user_account(8)

    listed = client.get("/api/v1/admin/accounts", headers=_headers(access))
    suspended = client.post(
        "/api/v1/admin/accounts/8/suspend", headers=_headers(access)
    )

    assert listed.status_code == suspended.status_code == 200
    assert listed.json()["items"][0]["email"] == "Person@example.com"
    assert suspended.json()["status"] == "suspended"
    assert repository.suspended_accounts == [8]
    assert repository.admin_audits == ["account_suspended"]


def test_admin_routes_do_not_require_internal_crawler_api_key() -> None:
    client, _repository, _licenses, access = _client(admin=True)

    response = client.get(
        "/api/v1/admin/license-keys", headers=_headers(access)
    )

    assert response.status_code == 200


def test_refreshing_access_token_does_not_satisfy_recent_password_authentication() -> None:
    client, repository, _licenses, access = _client(admin=True)
    repository.session = replace(
        repository.session,
        created_at=NOW - timedelta(hours=1),
        last_used_at=NOW,
        authenticated_at=NOW - timedelta(hours=1),
    )

    response = client.post(
        "/api/v1/admin/accounts/7/suspend", headers=_headers(access)
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Recent authentication required."


def test_admin_cannot_suspend_own_account() -> None:
    client, repository, _licenses, access = _client(admin=True)

    response = client.post(
        "/api/v1/admin/accounts/7/suspend", headers=_headers(access)
    )

    assert response.status_code == 403
    assert repository.suspended_accounts == []


def test_audit_failure_rolls_back_account_suspension() -> None:
    client, repository, licenses, access = _client(admin=True)
    repository.add_user_account(8)

    repository.fail_admin_audit = True
    response = client.post(
        "/api/v1/admin/accounts/8/suspend", headers=_headers(access)
    )

    assert response.status_code == 500
    assert repository.suspended_accounts == []


def test_admin_key_creation_is_rate_limited() -> None:
    client, _repository, _licenses, access = _client(admin=True)

    responses = [
        client.post(
            "/api/v1/admin/license-keys", headers=_headers(access), json=GRANT
        )
        for _ in range(11)
    ]

    assert [response.status_code for response in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429


def test_admin_lists_account_devices_before_revoking_one() -> None:
    client, repository, _licenses, access = _client(admin=True)

    listed = client.get(
        "/api/v1/admin/accounts/7/devices", headers=_headers(access)
    )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == repository.device.id
    assert listed.json()["items"][0]["current"] is True


def test_admin_reads_paginated_audit_events() -> None:
    client, _repository, _licenses, access = _client(admin=True)

    response = client.get(
        "/api/v1/admin/audit-events?limit=1", headers=_headers(access)
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["action"] == "license_key_created"
    assert response.json()["next_cursor"] == 41


def test_password_reauthentication_unlocks_recent_admin_operation() -> None:
    client, repository, _licenses, access = _client(admin=True)
    repository.add_user_account(8)
    repository.session = replace(
        repository.session,
        created_at=NOW - timedelta(hours=1),
        last_used_at=NOW,
        authenticated_at=NOW - timedelta(hours=1),
    )

    reauthenticated = client.post(
        "/api/v1/auth/reauthenticate",
        headers=_headers(access),
        json={"password": "correct horse battery staple"},
    )
    suspended = client.post(
        "/api/v1/admin/accounts/8/suspend", headers=_headers(access)
    )

    assert reauthenticated.status_code == 200
    assert suspended.status_code == 200
