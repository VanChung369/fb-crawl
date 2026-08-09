from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    ProviderResult,
    ProviderStatus,
    RetryCandidate,
    UserBundle,
)
from fb_data_pipeline.services.persistence import (
    PersistenceFailure,
    PersistenceReport,
)
from fb_data_pipeline.services.pipeline import (
    EnrichedUser,
    PipelineReport,
    PipelineRun,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def candidate(
    user_id: int = 1,
    *,
    status: ProviderStatus = ProviderStatus.FAILED,
) -> RetryCandidate:
    return RetryCandidate(
        user_id=user_id,
        identity=FacebookIdentity(
            uid=str(100 + user_id),
            username=f"sample.user.{user_id}",
        ),
        status=status,
        checked_at=NOW - timedelta(days=2),
        error_code="provider_transport_error",
    )


class Source:
    def __init__(
        self,
        *,
        lock_acquired: bool = True,
        candidates: tuple[RetryCandidate, ...] = (),
    ) -> None:
        self.lock_acquired = lock_acquired
        self.candidates = candidates
        self.lock_entered = 0
        self.lock_exited = 0
        self.list_calls: list[tuple[datetime | None, int]] = []

    @contextmanager
    def fbnumber_retry_lock(self):
        self.lock_entered += 1
        try:
            yield self.lock_acquired
        finally:
            self.lock_exited += 1

    def list_fbnumber_retry_candidates(
        self,
        *,
        eligible_before: datetime | None,
        limit: int,
    ) -> tuple[RetryCandidate, ...]:
        self.list_calls.append((eligible_before, limit))
        return self.candidates[:limit]


class Enrichment:
    def __init__(
        self,
        statuses: tuple[ProviderStatus, ...] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.statuses = statuses
        self.error = error
        self.calls: list[tuple[UserBundle, ...]] = []

    def run_bundles(
        self,
        bundles: tuple[UserBundle, ...],
        *,
        limit: int | None = None,
    ) -> PipelineRun:
        assert limit is None
        self.calls.append(bundles)
        if self.error is not None:
            raise self.error
        users = tuple(
            EnrichedUser(
                bundle=bundle,
                provider_result=ProviderResult(
                    provider="fbnumber",
                    status=status,
                    checked_at=NOW,
                ),
            )
            for bundle, status in zip(bundles, self.statuses, strict=True)
        )
        return PipelineRun(
            users=users,
            report=PipelineReport(
                users=len(users),
                input_records=len(bundles),
                skipped_records=0,
                invalid_crawler_phones=0,
                phone_1_found=0,
                phone_2_found=0,
                provider_found=sum(
                    status is ProviderStatus.FOUND for status in self.statuses
                ),
                provider_not_found=sum(
                    status is ProviderStatus.NOT_FOUND
                    for status in self.statuses
                ),
                provider_failed=sum(
                    status
                    in {ProviderStatus.FAILED, ProviderStatus.RATE_LIMITED}
                    for status in self.statuses
                ),
            ),
        )


class Persistence:
    def __init__(
        self,
        report: PersistenceReport | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.report = report or PersistenceReport(
            intended=0,
            persisted=0,
            provider_retries_required=0,
            user_ids=(),
        )
        self.error = error
        self.calls: list[PipelineRun] = []

    def persist(self, run: PipelineRun) -> PersistenceReport:
        self.calls.append(run)
        if self.error is not None:
            raise self.error
        return self.report


def test_busy_worker_returns_without_selecting_or_calling_provider() -> None:
    from fb_data_pipeline.services.retry import FBNumberRetryService

    source = Source(lock_acquired=False)
    enrichment = Enrichment()
    persistence = Persistence()

    report = FBNumberRetryService(
        source,
        enrichment,
        persistence,
        clock=lambda: NOW,
    ).run()

    assert report.worker_busy is True
    assert report.selected == 0
    assert source.list_calls == []
    assert enrichment.calls == []
    assert persistence.calls == []
    assert source.lock_exited == 1


def test_dry_run_selects_without_enrichment_or_persistence() -> None:
    from fb_data_pipeline.services.retry import FBNumberRetryService

    source = Source(candidates=(candidate(),))
    report = FBNumberRetryService(source, clock=lambda: NOW).run(dry_run=True)

    assert report.selected == 1
    assert report.dry_run is True
    assert report.persisted == 0
    assert report.exit_code == 0


def test_default_cooldown_and_force_control_candidate_cutoff() -> None:
    from fb_data_pipeline.services.retry import FBNumberRetryService

    normal = Source()
    FBNumberRetryService(normal, clock=lambda: NOW).run(dry_run=True)
    forced = Source()
    FBNumberRetryService(forced, clock=lambda: NOW).run(
        dry_run=True,
        force=True,
        limit=7,
    )

    assert normal.list_calls == [(NOW - timedelta(hours=24), 20)]
    assert forced.list_calls == [(None, 7)]


@pytest.mark.parametrize(
    ("limit", "cooldown"),
    [
        (0, timedelta(hours=24)),
        (-1, timedelta(hours=24)),
        (20, timedelta(seconds=-1)),
    ],
)
def test_invalid_retry_controls_fail_before_lock_acquisition(
    limit: int,
    cooldown: timedelta,
) -> None:
    from fb_data_pipeline.services.retry import FBNumberRetryService

    source = Source()

    with pytest.raises(ValueError):
        FBNumberRetryService(source, clock=lambda: NOW).run(
            dry_run=True,
            limit=limit,
            cooldown=cooldown,
        )

    assert source.lock_entered == 0


def test_retry_run_counts_statuses_and_database_failures() -> None:
    from fb_data_pipeline.services.retry import FBNumberRetryService

    candidates = tuple(candidate(index) for index in range(1, 5))
    statuses = (
        ProviderStatus.FOUND,
        ProviderStatus.NOT_FOUND,
        ProviderStatus.FAILED,
        ProviderStatus.RATE_LIMITED,
    )
    source = Source(candidates=candidates)
    enrichment = Enrichment(statuses)
    persistence = Persistence(
        PersistenceReport(
            intended=4,
            persisted=3,
            provider_retries_required=2,
            user_ids=(11, 12, 13),
            failures=(
                PersistenceFailure(
                    aliases=("uid:104",),
                    error_code="database_identity_conflict",
                ),
            ),
        )
    )

    report = FBNumberRetryService(
        source,
        enrichment,
        persistence,
        clock=lambda: NOW,
    ).run()

    assert enrichment.calls == [
        tuple(item.to_bundle() for item in candidates)
    ]
    assert len(persistence.calls) == 1
    assert report.selected == 4
    assert report.persisted == 3
    assert report.found == 1
    assert report.not_found == 1
    assert report.failed == 1
    assert report.rate_limited == 1
    assert report.retry_pending == 2
    assert report.database_failures == 1
    assert report.exit_code == 5


def test_retry_pending_without_database_failure_returns_exit_one() -> None:
    from fb_data_pipeline.services.retry import RetryReport

    assert RetryReport(failed=1).exit_code == 1
    assert RetryReport(rate_limited=1).exit_code == 1
    assert RetryReport(found=1, persisted=1).exit_code == 0


def test_missing_execution_dependencies_fail_inside_released_lock() -> None:
    from fb_data_pipeline.services.retry import FBNumberRetryService

    source = Source(candidates=(candidate(),))

    with pytest.raises(ValueError, match="dependencies are required"):
        FBNumberRetryService(source, clock=lambda: NOW).run()

    assert source.lock_exited == 1


@pytest.mark.parametrize("stage", ["enrichment", "persistence"])
def test_worker_lock_releases_when_execution_raises(stage: str) -> None:
    from fb_data_pipeline.services.retry import FBNumberRetryService

    source = Source(candidates=(candidate(),))
    enrichment_error = RuntimeError("enrichment failed")
    persistence_error = RuntimeError("persistence failed")
    enrichment = Enrichment(
        (ProviderStatus.FOUND,),
        error=enrichment_error if stage == "enrichment" else None,
    )
    persistence = Persistence(
        error=persistence_error if stage == "persistence" else None,
    )

    with pytest.raises(RuntimeError, match=f"{stage} failed"):
        FBNumberRetryService(
            source,
            enrichment,
            persistence,
            clock=lambda: NOW,
        ).run()

    assert source.lock_exited == 1
