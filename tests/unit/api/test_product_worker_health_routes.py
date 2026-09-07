from __future__ import annotations

from fb_crawl.exports.service import ExportWorkerHealth
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_product_auth_routes import product_client


class ExportHealthServiceFake:
    def __init__(self) -> None:
        self.calls = []

    def worker_health(self, now):
        self.calls.append(now)
        return ExportWorkerHealth(available=True, last_seen_at=NOW)


def headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_worker_health_returns_only_safe_export_liveness() -> None:
    service = ExportHealthServiceFake()
    client, _repository, _auth, access = product_client(export_service=service)

    response = client.get("/api/v1/worker-health", headers=headers(access))

    assert response.status_code == 200
    assert response.json() == {
        "workers": {
            "export": {
                "available": True,
                "last_seen_at": "2026-08-30T08:00:00Z",
            }
        }
    }
    assert service.calls == [NOW]


def test_worker_health_requires_product_bearer_authentication() -> None:
    client, _repository, _auth, _access = product_client(
        export_service=ExportHealthServiceFake()
    )

    response = client.get("/api/v1/worker-health")

    assert response.status_code == 401
