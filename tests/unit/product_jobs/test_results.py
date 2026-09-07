from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fb_crawl.accounts.models import DeviceStatus
from fb_crawl.contacts.models import LookupOutcome, LookupSource
from fb_crawl.contacts.service import ContactLookupResult
from fb_crawl.core.models import AuthenticatedAction, UserRecord
from fb_crawl.product_jobs.results import ProductJobResultSink


NOW = datetime(2026, 9, 1, 9, tzinfo=UTC)
PRODUCT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHILD_ID = UUID("22222222-2222-4222-8222-222222222222")


class Repository:
    def __init__(self) -> None:
        self.counts = None

    def owner_for_child(self, child_id):
        assert child_id == CHILD_ID
        return SimpleNamespace(
            product_job_id=PRODUCT_ID,
            account_id=7,
            max_identities=1000,
        )

    def add_result_counts(self, *args, **kwargs):
        self.counts = (args, kwargs)


class Accounts:
    account = SimpleNamespace(id=7)
    device = SimpleNamespace(id=9, status=DeviceStatus.ACTIVE)

    def get_account(self, account_id):
        return self.account if account_id == 7 else None

    def list_devices(self, account_id):
        return (self.device,) if account_id == 7 else ()


class Contacts:
    def __init__(self) -> None:
        self.calls = []

    def lookup(self, account, device, request, now, **kwargs):
        self.calls.append((account, device, request, now, kwargs))
        return ContactLookupResult(
            event_id=70 + len(self.calls),
            state=LookupOutcome.FOUND,
            source=LookupSource.CACHE,
            user=request.identity,
            phone="+84981234567",
        )


def test_result_sink_deduplicates_and_tags_automatic_history_context() -> None:
    repository = Repository()
    contacts = Contacts()
    sink = ProductJobResultSink(repository, Accounts(), contacts)
    record = UserRecord(
        "100001",
        "Sample User",
        "https://www.facebook.com/profile.php?id=100001",
        "members",
        "https://www.facebook.com/groups/123/members",
    )

    sink.record(
        CHILD_ID,
        AuthenticatedAction.MEMBERS,
        (record, record),
        NOW,
    )

    assert len(contacts.calls) == 1
    context = contacts.calls[0][-1]["scan_context"]
    assert context.mode == "automatic"
    assert context.source_type == "member"
    assert context.source_url == "https://www.facebook.com/groups/123/members"
    assert context.product_crawl_job_id == PRODUCT_ID
    assert repository.counts[1]["processed"] == 1
    assert repository.counts[1]["found"] == 1
