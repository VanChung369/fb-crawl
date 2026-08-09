from __future__ import annotations

import pytest

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict
from fb_data_pipeline.services.persistence import (
    PersistenceFailure,
    PersistenceReport,
    PipelinePersistenceService,
)
from fb_data_pipeline.services.pipeline import (
    EnrichedUser,
    PipelineReport,
    PipelineRun,
)


class RecordingRepository:
    def __init__(
        self,
        user_ids: tuple[int, ...],
        *,
        fail_at: int | None = None,
        conflict_at: int | None = None,
    ) -> None:
        self.user_ids = user_ids
        self.fail_at = fail_at
        self.conflict_at = conflict_at
        self.saved: list[EnrichedUser] = []
        self.successes = 0

    def save_enriched_user(self, enriched: EnrichedUser) -> int:
        self.saved.append(enriched)
        if self.fail_at == len(self.saved):
            raise RuntimeError("database unavailable")
        if self.conflict_at == len(self.saved):
            raise DatabaseIdentityConflict("Identity conflict.")
        user_id = self.user_ids[self.successes]
        self.successes += 1
        return user_id


def make_run(statuses: tuple[ProviderStatus, ...]) -> PipelineRun:
    users = tuple(
        EnrichedUser(
            bundle=UserBundle(
                identity=FacebookIdentity(
                    uid=str(index),
                    name=f"User {index}",
                )
            ),
            provider_result=ProviderResult(
                provider="fbnumber",
                status=status,
            ),
        )
        for index, status in enumerate(statuses, start=1)
    )
    return PipelineRun(
        users=users,
        report=PipelineReport(
            users=len(users),
            input_records=len(users),
            skipped_records=0,
            invalid_crawler_phones=0,
            phone_1_found=0,
            phone_2_found=0,
            provider_found=0,
            provider_not_found=0,
            provider_failed=0,
        ),
    )


def test_persistence_saves_in_order_and_reports_provider_retries() -> None:
    run = make_run(
        (
            ProviderStatus.FOUND,
            ProviderStatus.NOT_FOUND,
            ProviderStatus.FAILED,
        )
    )
    repository = RecordingRepository((101, 102, 103))

    report = PipelinePersistenceService(repository).persist(run)

    assert repository.saved == list(run.users)
    assert report == PersistenceReport(
        intended=3,
        persisted=3,
        provider_retries_required=1,
        user_ids=(101, 102, 103),
    )


def test_persistence_stops_and_propagates_repository_error() -> None:
    run = make_run(
        (
            ProviderStatus.FOUND,
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.FOUND,
        )
    )
    repository = RecordingRepository((101, 102, 103), fail_at=2)

    with pytest.raises(RuntimeError, match="database unavailable"):
        PipelinePersistenceService(repository).persist(run)

    assert repository.saved == list(run.users[:2])


def test_persistence_isolates_identity_conflict_and_continues() -> None:
    run = make_run((ProviderStatus.FOUND,) * 3)
    repository = RecordingRepository(
        (101, 103),
        conflict_at=2,
    )

    report = PipelinePersistenceService(repository).persist(run)

    assert repository.saved == list(run.users)
    assert report == PersistenceReport(
        intended=3,
        persisted=2,
        provider_retries_required=0,
        user_ids=(101, 103),
        failures=(
            PersistenceFailure(
                aliases=run.users[1].bundle.identity.aliases,
                error_code="database_identity_conflict",
            ),
        ),
    )
    assert report.db_failed == 1
