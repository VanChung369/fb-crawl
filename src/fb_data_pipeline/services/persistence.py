from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from fb_data_pipeline.core.models import ProviderStatus
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict

if TYPE_CHECKING:
    from fb_data_pipeline.services.pipeline import EnrichedUser, PipelineRun


class EnrichedUserRepository(Protocol):
    def save_enriched_user(self, enriched: EnrichedUser) -> int:
        pass


@dataclass(frozen=True, slots=True)
class PersistenceFailure:
    aliases: tuple[str, ...]
    error_code: str


@dataclass(frozen=True, slots=True)
class PersistenceReport:
    intended: int
    persisted: int
    provider_retries_required: int
    user_ids: tuple[int, ...]
    failures: tuple[PersistenceFailure, ...] = ()

    @property
    def db_failed(self) -> int:
        return len(self.failures)


class PipelinePersistenceService:
    def __init__(self, repository: EnrichedUserRepository) -> None:
        self.repository = repository

    def persist(self, run: PipelineRun) -> PersistenceReport:
        user_ids: list[int] = []
        failures: list[PersistenceFailure] = []
        retries = 0
        for enriched in run.users:
            try:
                user_ids.append(self.repository.save_enriched_user(enriched))
            except DatabaseIdentityConflict as error:
                failures.append(
                    PersistenceFailure(
                        aliases=enriched.bundle.identity.aliases,
                        error_code=error.code,
                    )
                )
            if enriched.provider_result.status in {
                ProviderStatus.RATE_LIMITED,
                ProviderStatus.FAILED,
            }:
                retries += 1
        return PersistenceReport(
            intended=len(run.users),
            persisted=len(user_ids),
            provider_retries_required=retries,
            user_ids=tuple(user_ids),
            failures=tuple(failures),
        )
