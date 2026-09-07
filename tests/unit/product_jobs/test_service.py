from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from fb_crawl.core.jobs import CrawlJob, JobStatus, SafeJobOptions
from fb_crawl.core.models import AuthenticatedAction
from fb_crawl.entitlements.models import Entitlements
from fb_crawl.product_jobs.models import (
    ProductCrawlJob,
    ProductCrawlScope,
    ProductCrawlStatus,
)
from fb_crawl.product_jobs.service import (
    CrawlUpgradeRequired,
    ProductCrawlJobService,
)


NOW = datetime(2026, 9, 1, 9, tzinfo=UTC)
PRODUCT_ID = UUID("11111111-1111-4111-8111-111111111111")


class Repository:
    def __init__(self) -> None:
        self.children = []

    def create(self, account_id, scope, target, maximum, now, **kwargs):
        return ProductCrawlJob(
            PRODUCT_ID, account_id, scope, target, maximum,
            kwargs.get("status", ProductCrawlStatus.QUEUED),
            0, 0, 0, 0, 0, kwargs.get("safe_error_code", ""),
            None, now, now,
            now if kwargs.get("status") is ProductCrawlStatus.BLOCKED else None,
        )

    def add_child(self, account_id, product_id, child_id, action):
        self.children.append((account_id, product_id, child_id, action))
        return True


class EntitlementService:
    def __init__(self, value):
        self.value = value

    def for_account(self, account_id, now):
        return self.value


class InternalJobs:
    def __init__(self) -> None:
        self.commands = []

    def create(self, command, *, idempotency_key):
        self.commands.append((command, idempotency_key))
        index = len(self.commands)
        return CrawlJob(
            UUID(f"22222222-2222-4222-8222-{index:012d}"),
            command.action,
            JobStatus.QUEUED,
            command.options,
            "fingerprint",
            NOW,
            NOW,
        ), True


def service(entitlements, *, session=True):
    repository = Repository()
    internal = InternalJobs()
    return (
        ProductCrawlJobService(
            repository,
            EntitlementService(entitlements),
            internal,
            object(),
            session_available=session,
        ),
        repository,
        internal,
    )


def test_default_plan_is_rejected_before_any_internal_job_is_created() -> None:
    value, repository, internal = service(
        Entitlements(100, 1, False, False)
    )

    with pytest.raises(CrawlUpgradeRequired):
        value.create(
            7,
            ProductCrawlScope.MEMBERS,
            "https://www.facebook.com/groups/123",
            1000,
            NOW,
        )

    assert internal.commands == repository.children == []


def test_paid_both_scope_creates_bounded_members_and_comments_children() -> None:
    value, repository, internal = service(
        Entitlements(500, 2, True, True)
    )

    job = value.create(
        7,
        ProductCrawlScope.BOTH,
        "https://www.facebook.com/groups/123/posts/456",
        1000,
        NOW,
    )

    assert job.status is ProductCrawlStatus.QUEUED
    assert [command.action for command, _key in internal.commands] == [
        AuthenticatedAction.MEMBERS,
        AuthenticatedAction.COMMENTS,
    ]
    assert all(
        command.options == SafeJobOptions(max_users=1000, call_fbnumber=True)
        for command, _key in internal.commands
    )
    assert len(repository.children) == 2


def test_missing_server_session_returns_a_safe_blocked_job() -> None:
    value, _repository, internal = service(
        Entitlements(500, 2, True, True),
        session=False,
    )

    job = value.create(
        7,
        ProductCrawlScope.MEMBERS,
        "https://www.facebook.com/groups/123",
        1000,
        NOW,
    )

    assert job.status is ProductCrawlStatus.BLOCKED
    assert job.safe_error_code == "facebook_session_unavailable"
    assert internal.commands == []
