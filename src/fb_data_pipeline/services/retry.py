from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ContextManager, Protocol

from fb_data_pipeline.core.models import (
    ProviderStatus,
    RetryCandidate,
    UserBundle,
)
from fb_data_pipeline.services.persistence import PersistenceReport
from fb_data_pipeline.services.pipeline import PipelineRun


def _now_utc() -> datetime:
    return datetime.now(UTC)


class RetrySourcePort(Protocol):
    def fbnumber_retry_lock(self) -> ContextManager[bool]: ...

    def list_fbnumber_retry_candidates(
        self,
        *,
        eligible_before: datetime | None,
        limit: int,
    ) -> tuple[RetryCandidate, ...]: ...


class EnrichmentPort(Protocol):
    def run_bundles(
        self,
        bundles: tuple[UserBundle, ...],
        *,
        limit: int | None = None,
    ) -> PipelineRun: ...


class PersistencePort(Protocol):
    def persist(self, run: PipelineRun) -> PersistenceReport: ...


@dataclass(frozen=True, slots=True)
class RetryReport:
    selected: int = 0
    persisted: int = 0
    found: int = 0
    not_found: int = 0
    failed: int = 0
    rate_limited: int = 0
    database_failures: int = 0
    dry_run: bool = False
    worker_busy: bool = False

    @property
    def retry_pending(self) -> int:
        return self.failed + self.rate_limited

    @property
    def exit_code(self) -> int:
        if self.database_failures:
            return 5
        if self.retry_pending:
            return 1
        return 0


class FBNumberRetryService:
    def __init__(
        self,
        source: RetrySourcePort,
        enrichment: EnrichmentPort | None = None,
        persistence: PersistencePort | None = None,
        *,
        clock: Callable[[], datetime] = _now_utc,
    ) -> None:
        self.source = source
        self.enrichment = enrichment
        self.persistence = persistence
        self.clock = clock

    def run(
        self,
        *,
        limit: int = 20,
        cooldown: timedelta = timedelta(hours=24),
        force: bool = False,
        dry_run: bool = False,
    ) -> RetryReport:
        if limit <= 0:
            raise ValueError("retry limit must be positive")
        if cooldown < timedelta(0):
            raise ValueError("retry cooldown must be zero or greater")

        eligible_before = None if force else self.clock() - cooldown
        with self.source.fbnumber_retry_lock() as acquired:
            if not acquired:
                return RetryReport(worker_busy=True)

            candidates = self.source.list_fbnumber_retry_candidates(
                eligible_before=eligible_before,
                limit=limit,
            )
            if dry_run:
                return RetryReport(
                    selected=len(candidates),
                    dry_run=True,
                )
            if not candidates:
                return RetryReport()
            if self.enrichment is None or self.persistence is None:
                raise ValueError(
                    "retry enrichment and persistence dependencies are required"
                )

            run = self.enrichment.run_bundles(
                tuple(candidate.to_bundle() for candidate in candidates)
            )
            persisted = self.persistence.persist(run)

            statuses = tuple(
                enriched.provider_result.status for enriched in run.users
            )
            return RetryReport(
                selected=len(candidates),
                persisted=persisted.persisted,
                found=sum(
                    status is ProviderStatus.FOUND for status in statuses
                ),
                not_found=sum(
                    status is ProviderStatus.NOT_FOUND for status in statuses
                ),
                failed=sum(
                    status is ProviderStatus.FAILED for status in statuses
                ),
                rate_limited=sum(
                    status is ProviderStatus.RATE_LIMITED
                    for status in statuses
                ),
                database_failures=persisted.db_failed,
            )
