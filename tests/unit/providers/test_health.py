from __future__ import annotations

from datetime import UTC, datetime

from fb_crawl.providers.health import ObservedPhoneProvider
from fb_data_pipeline.core.models import FacebookIdentity, ProviderResult, ProviderStatus


NOW = datetime(2026, 9, 2, 2, tzinfo=UTC)


class Provider:
    name = "fbnumber"

    def __init__(self, result: ProviderResult) -> None:
        self.result = result
        self.calls = 0

    def search(self, _identity):
        self.calls += 1
        return self.result

    def close(self):
        return None


class Repository:
    def __init__(self) -> None:
        self.records = []

    def record(self, **values):
        self.records.append(values)


def test_provider_401_records_only_safe_error_without_retrying() -> None:
    provider = Provider(ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FAILED,
        checked_at=NOW,
        error_code="provider_http_401",
    ))
    repository = Repository()
    observed = ObservedPhoneProvider(provider, repository, configured=True)

    result = observed.search(FacebookIdentity(uid="10001"))

    assert result.error_code == "provider_http_401"
    assert provider.calls == 1
    assert repository.records == [{
        "provider_name": "fbnumber",
        "configured": True,
        "success_at": None,
        "safe_error_code": "provider_http_401",
        "updated_at": NOW,
    }]
    assert "token" not in repr(repository.records).casefold()


def test_found_or_not_found_updates_last_success_and_clears_error() -> None:
    repository = Repository()
    observed = ObservedPhoneProvider(
        Provider(ProviderResult(
            provider="fbnumber",
            status=ProviderStatus.NOT_FOUND,
            checked_at=NOW,
        )),
        repository,
        configured=True,
    )

    observed.search(FacebookIdentity(uid="10001"))

    assert repository.records[0]["success_at"] == NOW
    assert repository.records[0]["safe_error_code"] == ""
