from __future__ import annotations

from fb_crawl.core.models import ScrapeResult, ScrapeStats, UserRecord
from fb_data_pipeline.services.ingestion import (
    AuthenticatedIngestionService,
    IngestionReport,
)
from fb_data_pipeline.services.persistence import (
    PersistenceFailure,
    PersistenceReport,
)
from fb_data_pipeline.services.pipeline import PipelineReport, PipelineRun


def scrape_result() -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=(
            UserRecord(
                user_id="100",
                name="Synthetic User",
                username="synthetic.user",
                profile_url="https://www.facebook.com/synthetic.user",
                source="friends",
                source_url="https://www.facebook.com/source/friends",
            ),
        ),
        issues=(),
        stats=ScrapeStats(
            requested=1,
            discovered=1,
            succeeded=1,
            failed=0,
        ),
    )


def pipeline_report() -> PipelineReport:
    return PipelineReport(
        users=1,
        input_records=1,
        skipped_records=0,
        invalid_crawler_phones=0,
        phone_1_found=0,
        phone_2_found=0,
        provider_found=0,
        provider_not_found=0,
        provider_failed=1,
    )


class RecordingEnrichment:
    def __init__(self, run: PipelineRun) -> None:
        self.run = run
        self.calls: list[tuple[object, str]] = []

    def run_scrape_result(
        self,
        result,
        *,
        default_country_code: str,
    ) -> PipelineRun:
        self.calls.append((result, default_country_code))
        return self.run


class RecordingPersistence:
    def __init__(self, report: PersistenceReport) -> None:
        self.report = report
        self.calls: list[PipelineRun] = []

    def persist(self, run: PipelineRun) -> PersistenceReport:
        self.calls.append(run)
        return self.report


def test_ingestion_passes_typed_result_through_pipeline_and_persistence() -> None:
    source = scrape_result()
    run = PipelineRun(users=(), report=pipeline_report())
    persisted = PersistenceReport(
        intended=1,
        persisted=1,
        provider_retries_required=1,
        user_ids=(41,),
    )
    enrichment = RecordingEnrichment(run)
    persistence = RecordingPersistence(persisted)
    service = AuthenticatedIngestionService(enrichment, persistence)

    report = service.ingest(source, default_country_code="84")

    assert enrichment.calls == [(source, "84")]
    assert persistence.calls == [run]
    assert report == IngestionReport(
        pipeline=run.report,
        persistence=persisted,
    )
    assert report.has_provider_retries is True
    assert report.has_database_failures is False


def test_ingestion_reports_isolated_database_failures() -> None:
    run = PipelineRun(users=(), report=pipeline_report())
    persisted = PersistenceReport(
        intended=1,
        persisted=0,
        provider_retries_required=0,
        user_ids=(),
        failures=(
            PersistenceFailure(
                aliases=("uid:100",),
                error_code="database_identity_conflict",
            ),
        ),
    )
    service = AuthenticatedIngestionService(
        RecordingEnrichment(run),
        RecordingPersistence(persisted),
    )

    report = service.ingest(scrape_result())

    assert report.has_database_failures is True
