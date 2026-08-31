from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from fb_crawl.contacts.models import LookupOutcome, LookupSource
from fb_crawl.core.jobs import Page
from fb_crawl.entitlements.models import Entitlements
from fb_crawl.entitlements.quota import RevealDecision
from fb_crawl.history.models import HistoryItem
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_product_auth_routes import product_client


class HistoryRepositoryFake:
    def __init__(self) -> None:
        self.item = HistoryItem(
            71, 7, 9, 41, "100123", "sample.user", "Sample User",
            "https://www.facebook.com/sample.user", 81, "+84981234567",
            LookupOutcome.FOUND, LookupSource.CACHE, False, True, "", NOW,
            NOW + timedelta(seconds=1),
        )
        self.queries = []
        self.deleted: list[tuple[int, int]] = []
        self.filtered = []
        self.visible = True

    def list(self, query):
        self.queries.append(query)
        return Page((self.item,), "next-token")

    def get(self, account_id, event_id):
        return self.item if self.visible and (account_id, event_id) == (7, 71) else None

    def delete_one(self, account_id, event_id):
        self.deleted.append((account_id, event_id))
        return self.visible and (account_id, event_id) == (7, 71)

    def delete_filtered(self, query):
        self.filtered.append(query)
        return 3


class QuotaFake:
    def reserve(self, account_id, user_id, phone_id, event_id, now):
        return RevealDecision(True, False, 91, 1, 100)


class OneDeviceEntitlements:
    def for_account(self, _account_id, _now):
        return Entitlements(100, 1, False, False)


def headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_history_list_forces_authenticated_account_into_query() -> None:
    history = HistoryRepositoryFake()
    client, _repository, _auth, access = product_client(
        history_repository=history,
        quota_service=QuotaFake(),
    )

    response = client.get(
        "/api/v1/history/lookups?outcome=found&uid=100&limit=20",
        headers=headers(access),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["phone"] == "+84981234567"
    assert response.json()["next_cursor"] == "next-token"
    assert history.queries[0].account_id == 7
    assert history.queries[0].uid == "100"


def test_history_get_and_delete_hide_foreign_or_missing_event() -> None:
    history = HistoryRepositoryFake()
    history.visible = False
    client, _repository, _auth, access = product_client(
        history_repository=history,
        quota_service=QuotaFake(),
    )

    fetched = client.get(
        "/api/v1/history/lookups/71", headers=headers(access)
    )
    deleted = client.delete(
        "/api/v1/history/lookups/71", headers=headers(access)
    )

    assert fetched.status_code == deleted.status_code == 404


def test_filtered_history_delete_requires_explicit_confirmation() -> None:
    history = HistoryRepositoryFake()
    client, _repository, _auth, access = product_client(
        history_repository=history,
        quota_service=QuotaFake(),
    )

    denied = client.request(
        "DELETE",
        "/api/v1/history/lookups?outcome=failed",
        headers=headers(access),
        json={"confirm": False},
    )
    allowed = client.request(
        "DELETE",
        "/api/v1/history/lookups?outcome=failed",
        headers=headers(access),
        json={"confirm": True},
    )

    assert denied.status_code == 400
    assert allowed.status_code == 200
    assert allowed.json() == {"deleted_count": 3}
    assert len(history.filtered) == 1
    assert history.filtered[0].account_id == 7


def test_history_requires_bearer_even_for_safe_reads() -> None:
    history = HistoryRepositoryFake()
    client, _repository, _auth, access = product_client(
        history_repository=history,
        quota_service=QuotaFake(),
    )
    client.cookies.set("lead_finder_access", access)

    response = client.get(
        "/api/v1/history/lookups",
        headers={"X-Installation-ID": str(INSTALLATION_ID)},
    )

    assert response.status_code == 401
    assert history.queries == []


def test_history_rejects_an_authenticated_device_over_the_plan_limit() -> None:
    history = HistoryRepositoryFake()
    client, repository, _auth, access = product_client(
        history_repository=history,
        quota_service=QuotaFake(),
        entitlement_service=OneDeviceEntitlements(),
    )
    repository.extra_devices.append(
        replace(
            repository.device,
            id=8,
            first_seen_at=repository.device.first_seen_at - timedelta(days=1),
        )
    )

    response = client.get(
        "/api/v1/history/lookups",
        headers=headers(access),
    )

    assert response.status_code == 403
    assert history.queries == []
