from __future__ import annotations

from uuid import UUID

from fb_crawl.entitlements.models import Entitlements
from fb_crawl.product_jobs.models import ProductCrawlJob, ProductCrawlScope, ProductCrawlStatus
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_history_routes import HistoryRepositoryFake, QuotaFake
from tests.unit.api.test_product_auth_routes import product_client


JOB_ID = UUID("11111111-1111-4111-8111-111111111111")


class ProductCrawlRepositoryFake:
    def get(self, account_id, job_id):
        if (account_id, job_id) != (7, JOB_ID):
            return None
        return ProductCrawlJob(
            JOB_ID, 7, ProductCrawlScope.MEMBERS,
            "https://www.facebook.com/groups/123/members", 1000,
            ProductCrawlStatus.BLOCKED, 0, 0, 0, 0, 0,
            "facebook_session_unavailable", None, NOW, NOW, NOW,
        )


class EntitlementServiceFake:
    def for_account(self, _account_id, _now):
        return Entitlements(500, 2, True, True)


def headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_crawl_job_results_are_tenant_scoped_and_filtered_by_job() -> None:
    history = HistoryRepositoryFake()
    client, _repository, _auth, access = product_client(
        history_repository=history,
        quota_service=QuotaFake(),
        entitlement_service=EntitlementServiceFake(),
        product_crawl_repository=ProductCrawlRepositoryFake(),
    )

    response = client.get(
        f"/api/v1/crawl-jobs/{JOB_ID}/results?limit=25",
        headers=headers(access),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["product_crawl_job_id"] == str(JOB_ID)
    assert history.queries[0].account_id == 7
    assert history.queries[0].product_crawl_job_id == JOB_ID
    assert history.queries[0].limit == 25


def test_crawl_job_results_hide_unknown_or_foreign_jobs() -> None:
    history = HistoryRepositoryFake()
    client, _repository, _auth, access = product_client(
        history_repository=history,
        quota_service=QuotaFake(),
        entitlement_service=EntitlementServiceFake(),
        product_crawl_repository=ProductCrawlRepositoryFake(),
    )

    response = client.get(
        "/api/v1/crawl-jobs/22222222-2222-4222-8222-222222222222/results",
        headers=headers(access),
    )

    assert response.status_code == 404
    assert history.queries == []
