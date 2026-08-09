from __future__ import annotations

from dataclasses import dataclass

from fb_crawl.core.models import PageRecord, ScrapeResult, UserRecord
from fb_data_pipeline.core.models import (
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.importers.crawler import import_scrape_result
from fb_data_pipeline.providers.base import PhoneEnrichmentProvider
from fb_data_pipeline.services.merge import merge_bundles, merge_evidence


@dataclass(frozen=True, slots=True)
class EnrichedUser:
    bundle: UserBundle
    provider_result: ProviderResult


@dataclass(frozen=True, slots=True)
class PipelineReport:
    users: int
    input_records: int
    skipped_records: int
    invalid_crawler_phones: int
    phone_1_found: int
    phone_2_found: int
    provider_found: int
    provider_not_found: int
    provider_failed: int


@dataclass(frozen=True, slots=True)
class PipelineRun:
    users: tuple[EnrichedUser, ...]
    report: PipelineReport


class EnrichmentPipeline:
    def __init__(self, provider: PhoneEnrichmentProvider) -> None:
        self.provider = provider

    def _run(
        self,
        bundles: tuple[UserBundle, ...],
        *,
        input_records: int,
        skipped_records: int,
        invalid_crawler_phones: int,
        limit: int | None = None,
    ) -> PipelineRun:
        merged = merge_bundles(bundles)
        if limit is not None:
            merged = merged[:limit]

        enriched: list[EnrichedUser] = []
        provider_found = 0
        provider_not_found = 0
        provider_failed = 0

        for original in merged:
            provider_result = self.provider.search(original.identity)
            if provider_result.status is ProviderStatus.FOUND:
                provider_found += 1
            elif provider_result.status is ProviderStatus.NOT_FOUND:
                provider_not_found += 1
            else:
                provider_failed += 1

            combined = UserBundle(
                identity=original.identity,
                evidence=merge_evidence(
                    original.evidence,
                    provider_result.evidence,
                ),
            )
            enriched.append(
                EnrichedUser(
                    bundle=combined,
                    provider_result=provider_result,
                )
            )

        return PipelineRun(
            users=tuple(enriched),
            report=PipelineReport(
                users=len(enriched),
                input_records=input_records,
                skipped_records=skipped_records,
                invalid_crawler_phones=invalid_crawler_phones,
                phone_1_found=sum(
                    item.bundle.phone_1 is not None for item in enriched
                ),
                phone_2_found=sum(
                    item.bundle.phone_2 is not None for item in enriched
                ),
                provider_found=provider_found,
                provider_not_found=provider_not_found,
                provider_failed=provider_failed,
            ),
        )

    def run_bundles(
        self,
        bundles: tuple[UserBundle, ...],
        *,
        limit: int | None = None,
    ) -> PipelineRun:
        return self._run(
            bundles,
            input_records=len(bundles),
            skipped_records=0,
            invalid_crawler_phones=0,
            limit=limit,
        )

    def run_scrape_result(
        self,
        result: ScrapeResult[UserRecord] | ScrapeResult[PageRecord],
        *,
        default_country_code: str = "84",
        limit: int | None = None,
    ) -> PipelineRun:
        imported = import_scrape_result(
            result,
            default_country_code=default_country_code,
        )
        return self._run(
            imported.bundles,
            input_records=imported.records_read,
            skipped_records=imported.records_skipped,
            invalid_crawler_phones=imported.invalid_phones,
            limit=limit,
        )

