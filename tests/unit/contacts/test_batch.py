from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fb_crawl.contacts.batch import (
    BatchContactItem,
    BatchContactLookupService,
)
from fb_crawl.contacts.models import LookupOutcome, LookupSource
from fb_crawl.contacts.service import ContactLookupRequest, ContactLookupResult
from fb_data_pipeline.core.models import FacebookIdentity


NOW = datetime(2026, 9, 1, 9, tzinfo=UTC)
ACCOUNT = SimpleNamespace(id=7)
DEVICE = SimpleNamespace(id=9)


class Lookup:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def lookup(
        self,
        account,
        device,
        request,
        now,
        *,
        force_refresh=False,
        scan_context=None,
    ) -> ContactLookupResult:
        self.calls.append((account, device, request, now, scan_context))
        return ContactLookupResult(
            event_id=70 + len(self.calls),
            state=LookupOutcome.FOUND,
            source=LookupSource.CACHE,
            user=request.identity,
            phone=f"+8490000000{len(self.calls)}",
            monthly_used=len(self.calls),
            monthly_limit=100,
        )


def batch_item(
    username: str,
    *,
    source_type: str = "member",
) -> BatchContactItem:
    return BatchContactItem(
        request=ContactLookupRequest(
            username=username,
            profile_url=f"https://www.facebook.com/{username}",
        ),
        source_type=source_type,
        source_url="https://www.facebook.com/groups/123/members",
    )


def test_batch_preserves_order_deduplicates_and_records_manual_context() -> None:
    lookup = Lookup()
    service = BatchContactLookupService(lookup)

    result = service.lookup(
        ACCOUNT,
        DEVICE,
        (batch_item("first.user"), batch_item("first.user"), batch_item("second.user")),
        NOW,
    )

    assert result.detected_count == 3
    assert result.unique_count == result.processed_count == 2
    assert result.found_count == 2
    assert [item.duplicate_of for item in result.items] == [None, 0, None]
    assert [item.result.user.username for item in result.items] == [
        "first.user",
        "first.user",
        "second.user",
    ]
    assert len(lookup.calls) == 2
    context = lookup.calls[0][-1]
    assert context.mode == "manual_loaded"
    assert context.source_type == "member"
    assert context.source_url == "https://www.facebook.com/groups/123/members"
    assert context.product_crawl_job_id is None


def test_batch_counts_quota_exhaustion_and_keeps_default_limit_visible() -> None:
    class QuotaLookup(Lookup):
        def lookup(self, *args, **kwargs):
            value = super().lookup(*args, **kwargs)
            if len(self.calls) == 2:
                return ContactLookupResult(
                    event_id=value.event_id,
                    state=LookupOutcome.QUOTA_EXCEEDED,
                    source=LookupSource.NONE,
                    user=FacebookIdentity(username="second.user"),
                    monthly_used=100,
                    monthly_limit=100,
                    safe_error_code="contact_quota_exhausted",
                )
            return value

    result = BatchContactLookupService(QuotaLookup()).lookup(
        ACCOUNT,
        DEVICE,
        (batch_item("first.user"), batch_item("second.user")),
        NOW,
    )

    assert result.found_count == 1
    assert result.quota_exceeded_count == 1
    assert result.items[-1].result.monthly_limit == 100
