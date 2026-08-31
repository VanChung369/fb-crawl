from __future__ import annotations

from dataclasses import replace

from fb_crawl.accounts.models import AccountRole
from fb_crawl.exports.metrics import ProductMetrics
from tests.unit.api.product_auth_fakes import INSTALLATION_ID
from tests.unit.api.test_product_auth_routes import product_client


class Metrics:
    def get(self):
        return ProductMetrics(*range(1, 27))


def headers(access):
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_product_metrics_reject_regular_user() -> None:
    client, _repository, _auth, access = product_client(
        metrics_repository=Metrics()
    )

    response = client.get(
        "/api/v1/admin/product-metrics", headers=headers(access)
    )

    assert response.status_code == 403


def test_product_metrics_returns_aggregate_counters_to_admin() -> None:
    client, repository, _auth, access = product_client(
        metrics_repository=Metrics()
    )
    repository.account = replace(repository.account, role=AccountRole.ADMIN)

    response = client.get(
        "/api/v1/admin/product-metrics", headers=headers(access)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accounts_total"] == 1
    assert body["provider_latency_average_ms"] == 19
    assert body["exports_expired"] == 26
