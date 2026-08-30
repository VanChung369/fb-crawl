from __future__ import annotations

from datetime import timedelta

from fb_crawl.entitlements.models import Entitlements
from fb_crawl.licenses.models import (
    LicenseDuration,
    LicenseGrant,
    LicenseKey,
    LicenseKeyStatus,
    Subscription,
    SubscriptionStatus,
)
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_product_auth_routes import product_client


class EntitlementServiceFake:
    def __init__(self, entitlements: Entitlements) -> None:
        self.entitlements = entitlements

    def for_account(self, account_id: int, now):
        return self.entitlements


class LicenseServiceFake:
    def __init__(self) -> None:
        self.grant = LicenseGrant(LicenseDuration("day", 7), 500, 2, True, False)
        self.key = LicenseKey(
            11,
            "private-digest",
            1,
            "LF-****-****-ABCD",
            self.grant,
            LicenseKeyStatus.AVAILABLE,
            7,
            None,
            None,
            NOW,
            None,
        )
        self.subscription = Subscription(
            21,
            7,
            11,
            self.grant,
            NOW,
            NOW + timedelta(days=7),
            SubscriptionStatus.VALID,
            None,
            NOW,
        )
        self.redeemed: list[tuple[int, str]] = []

    def redeem(self, account_id: int, plaintext: str, now):
        self.redeemed.append((account_id, plaintext))
        return self.subscription


def _headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_user_redeems_license_and_reads_effective_entitlements() -> None:
    licenses = LicenseServiceFake()
    entitlements = EntitlementServiceFake(
        Entitlements(500, 2, True, False, 21, NOW, NOW + timedelta(days=7))
    )
    client, _repository, _auth, access = product_client(
        license_service=licenses,
        entitlement_service=entitlements,
    )

    redeemed = client.post(
        "/api/v1/licenses/redeem",
        headers=_headers(access),
        json={"key": "LF-REAL-PLAINTEXT-ABCD"},
    )
    effective = client.get(
        "/api/v1/account/entitlements", headers=_headers(access)
    )

    assert redeemed.status_code == effective.status_code == 200
    assert redeemed.json()["subscription_id"] == 21
    assert effective.json()["monthly_contact_limit"] == 500
    assert effective.json()["allow_group_crawl"] is True
    assert licenses.redeemed == [(7, "LF-REAL-PLAINTEXT-ABCD")]


def test_account_me_marks_devices_outside_oldest_entitlement_slots() -> None:
    entitlements = EntitlementServiceFake(Entitlements(100, 1, False, False))
    client, repository, _auth, access = product_client(
        entitlement_service=entitlements
    )
    repository.extra_devices.append(
        repository.device.__class__(
            id=8,
            account_id=7,
            installation_id=repository.device.installation_id.__class__(
                "87654321-4321-4765-8765-876543218765"
            ),
            display_name="Older Chrome",
            status=repository.device.status,
            first_seen_at=NOW - timedelta(days=1),
            last_seen_at=NOW,
        )
    )

    response = client.get("/api/v1/account/me", headers=_headers(access))

    assert response.status_code == 200
    assert response.json()["device_allowed"] is False
