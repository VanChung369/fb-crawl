from datetime import UTC, datetime

from fb_crawl.entitlements.models import Entitlements
from fb_crawl.entitlements.service import EntitlementService


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)


class EntitlementRepository:
    def __init__(self, entitlements: Entitlements) -> None:
        self.entitlements = entitlements
        self.calls: list[tuple[int, datetime]] = []

    def effective_entitlements(
        self, account_id: int, now: datetime
    ) -> Entitlements:
        self.calls.append((account_id, now))
        return self.entitlements


def test_for_account_returns_effective_repository_snapshot() -> None:
    expected = Entitlements(1000, 3, True, True, subscription_id=17)
    repository = EntitlementRepository(expected)

    actual = EntitlementService(repository).for_account(7, NOW)

    assert actual is expected
    assert repository.calls == [(7, NOW)]
    assert actual.allow_auto_group_crawl is True
    assert actual.allow_auto_comment_crawl is True
    assert actual.max_auto_crawl_identities == 1000


def test_default_entitlements_keep_manual_scan_but_disable_automatic_crawl() -> None:
    value = Entitlements(100, 1, False, False)

    assert value.allow_auto_group_crawl is False
    assert value.allow_auto_comment_crawl is False
    assert value.max_auto_crawl_identities == 0
