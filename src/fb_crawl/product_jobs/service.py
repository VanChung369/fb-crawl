from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import (
    JobCreateCommand,
    JobStatus,
    SafeJobOptions,
    canonical_job_target,
)
from fb_crawl.core.models import AuthenticatedAction
from fb_crawl.core.urls import normalize_comments_url, normalize_members_url
from fb_crawl.product_jobs.models import (
    ProductCrawlJob,
    ProductCrawlScope,
    ProductCrawlStatus,
)


class CrawlUpgradeRequired(ValidationError):
    code = "crawl_upgrade_required"


class ProductCrawlJobService:
    def __init__(
        self,
        repository,
        entitlements,
        internal_jobs,
        internal_repository,
        *,
        session_available: bool,
    ) -> None:
        self.repository = repository
        self.entitlements = entitlements
        self.internal_jobs = internal_jobs
        self.internal_repository = internal_repository
        self.session_available = session_available

    def create(
        self,
        account_id: int,
        scope: ProductCrawlScope,
        target_url: str,
        max_identities: int,
        now: datetime,
    ) -> ProductCrawlJob:
        entitlement = self.entitlements.for_account(account_id, now)
        self._require_entitlement(entitlement, scope, max_identities)
        targets = _targets(scope, target_url)
        canonical_target = (
            targets[0][1] if len(targets) == 1 else _canonical_input(target_url)
        )
        if not self.session_available:
            return self.repository.create(
                account_id,
                scope,
                canonical_target,
                max_identities,
                now,
                status=ProductCrawlStatus.BLOCKED,
                safe_error_code="facebook_session_unavailable",
            )
        product_job = self.repository.create(
            account_id, scope, canonical_target, max_identities, now
        )
        for action, target in targets:
            command = JobCreateCommand(
                action=action,
                targets=(canonical_job_target(action, target),),
                options=SafeJobOptions(
                    max_users=max_identities,
                    call_fbnumber=True,
                ),
            )
            child, _created = self.internal_jobs.create(
                command,
                idempotency_key=f"product-{product_job.id}-{action.value}",
            )
            self.repository.add_child(
                account_id, product_job.id, child.id, action.value
            )
        return product_job

    def get(
        self, account_id: int, job_id: UUID, now: datetime
    ) -> ProductCrawlJob | None:
        job = self.repository.get(account_id, job_id)
        if job is None or job.status is ProductCrawlStatus.BLOCKED:
            return job
        children = tuple(
            child
            for child_id in self.repository.child_ids(account_id, job_id)
            if (child := self.internal_repository.get_job(child_id)) is not None
        )
        if not children:
            return job
        status = _combined_status(tuple(child.status for child in children))
        safe_error = next(
            (child.error_code for child in children if child.error_code), ""
        )
        return self.repository.update_from_children(
            account_id,
            job_id,
            status=status,
            discovered_count=min(
                job.max_identities,
                sum(child.discovered_users for child in children),
            ),
            processed_count=min(
                job.max_identities,
                sum(child.persisted_users for child in children),
            ),
            safe_error_code=safe_error,
            now=now,
        )

    def cancel(
        self, account_id: int, job_id: UUID, now: datetime
    ) -> ProductCrawlJob | None:
        job = self.repository.request_cancel(account_id, job_id, now)
        if job is None:
            return None
        for child_id in self.repository.child_ids(account_id, job_id):
            try:
                self.internal_jobs.cancel(child_id)
            except ValidationError:
                pass
        return self.get(account_id, job_id, now) or job

    @staticmethod
    def _require_entitlement(entitlement, scope, max_identities) -> None:
        allowed = (
            scope is ProductCrawlScope.MEMBERS
            and entitlement.allow_auto_group_crawl
        ) or (
            scope is ProductCrawlScope.ENGAGEMENT
            and entitlement.allow_auto_comment_crawl
        ) or (
            scope is ProductCrawlScope.BOTH
            and entitlement.allow_auto_group_crawl
            and entitlement.allow_auto_comment_crawl
        )
        if not allowed:
            raise CrawlUpgradeRequired("Automatic crawl requires a paid plan.")
        if not 1 <= max_identities <= entitlement.max_auto_crawl_identities:
            raise ValidationError("Invalid automatic crawl identity limit.")


def _targets(
    scope: ProductCrawlScope, target_url: str
) -> tuple[tuple[AuthenticatedAction, str], ...]:
    members = normalize_members_url(target_url)
    comments = normalize_comments_url(target_url)
    if scope in {ProductCrawlScope.MEMBERS, ProductCrawlScope.BOTH} and not members:
        parsed = urlsplit(target_url)
        parts = parsed.path.split("/")
        if len(parts) >= 3 and parts[1].casefold() == "groups":
            members = normalize_members_url(
                f"https://www.facebook.com/groups/{parts[2]}/members"
            )
    values: list[tuple[AuthenticatedAction, str]] = []
    if scope in {ProductCrawlScope.MEMBERS, ProductCrawlScope.BOTH}:
        if not members:
            raise ValidationError("Invalid automatic members target.")
        values.append((AuthenticatedAction.MEMBERS, members))
    if scope in {ProductCrawlScope.ENGAGEMENT, ProductCrawlScope.BOTH}:
        if not comments:
            raise ValidationError("Invalid automatic engagement target.")
        values.append((AuthenticatedAction.COMMENTS, comments))
    return tuple(values)


def _canonical_input(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {
        "facebook.com", "www.facebook.com", "m.facebook.com"
    }:
        raise ValidationError("Invalid automatic crawl target.")
    return f"https://www.facebook.com{parsed.path.rstrip('/')}"


def _combined_status(statuses: tuple[JobStatus, ...]) -> ProductCrawlStatus:
    if any(value is JobStatus.BLOCKED for value in statuses):
        return ProductCrawlStatus.BLOCKED
    if any(value is JobStatus.RUNNING or value is JobStatus.CANCELLING for value in statuses):
        return ProductCrawlStatus.RUNNING
    if any(value is JobStatus.QUEUED for value in statuses):
        return ProductCrawlStatus.QUEUED
    if all(value is JobStatus.CANCELLED for value in statuses):
        return ProductCrawlStatus.CANCELLED
    if all(value is JobStatus.SUCCEEDED for value in statuses):
        return ProductCrawlStatus.SUCCEEDED
    if any(value is JobStatus.PARTIAL or value is JobStatus.SUCCEEDED for value in statuses):
        return ProductCrawlStatus.PARTIAL
    return ProductCrawlStatus.FAILED
