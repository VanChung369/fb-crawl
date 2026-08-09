from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fb_crawl.core.models import ScrapeResult, UserRecord
from fb_data_pipeline.services.persistence import PersistenceReport
from fb_data_pipeline.services.pipeline import PipelineReport, PipelineRun


class EnrichmentPort(Protocol):
    def run_scrape_result(
        self,
        result: ScrapeResult[UserRecord],
        *,
        default_country_code: str = "84",
    ) -> PipelineRun: ...


class PersistencePort(Protocol):
    def persist(self, run: PipelineRun) -> PersistenceReport: ...


@dataclass(frozen=True, slots=True)
class IngestionReport:
    pipeline: PipelineReport
    persistence: PersistenceReport

    @property
    def has_provider_retries(self) -> bool:
        return self.persistence.provider_retries_required > 0

    @property
    def has_database_failures(self) -> bool:
        return self.persistence.db_failed > 0


class AuthenticatedIngestionService:
    def __init__(
        self,
        enrichment: EnrichmentPort,
        persistence: PersistencePort,
    ) -> None:
        self.enrichment = enrichment
        self.persistence = persistence

    def ingest(
        self,
        result: ScrapeResult[UserRecord],
        *,
        default_country_code: str = "84",
    ) -> IngestionReport:
        run = self.enrichment.run_scrape_result(
            result,
            default_country_code=default_country_code,
        )
        persisted = self.persistence.persist(run)
        return IngestionReport(
            pipeline=run.report,
            persistence=persisted,
        )
