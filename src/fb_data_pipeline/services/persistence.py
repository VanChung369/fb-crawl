from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from fb_data_pipeline.core.models import ProviderStatus

if TYPE_CHECKING:
    from fb_data_pipeline.services.pipeline import EnrichedUser, PipelineRun


class EnrichedUserRepository(Protocol):
    def save_enriched_user(self, enriched: EnrichedUser) -> int:
        pass


@dataclass(frozen=True, slots=True)
class PersistenceReport:
    intended: int
    persisted: int
    provider_retries_required: int
    user_ids: tuple[int, ...]


class PipelinePersistenceService:
    def __init__(self, repository: EnrichedUserRepository) -> None:
        self.repository = repository

    def persist(self, run: PipelineRun) -> PersistenceReport:
        user_ids: list[int] = []
        retries = 0
        for enriched in run.users:
            user_ids.append(self.repository.save_enriched_user(enriched))
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
        )
