from datetime import UTC, datetime

from fb_crawl.core.models import (
    PhoneEvidence as CrawlPhoneEvidence,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProviderResult,
    ProviderStatus,
)
from fb_data_pipeline.services.pipeline import EnrichmentPipeline


class FakeProvider:
    name = "fbnumber"

    def __init__(self, result: ProviderResult) -> None:
        self.result = result
        self.identities: list[FacebookIdentity] = []

    def search(self, identity: FacebookIdentity) -> ProviderResult:
        self.identities.append(identity)
        return self.result

    def close(self) -> None:
        pass


def scrape_result() -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=(
            UserRecord(
                user_id="10001",
                username="a.user",
                name="A",
                profile_url="https://www.facebook.com/a.user",
                source="profile",
                source_url="https://www.facebook.com/a.user",
                address="Ha Noi",
                birth_date="12 thang 8, 1990",
                gender="Nam",
                phone_numbers=("0912 345 678",),
                phone_evidence=(
                    CrawlPhoneEvidence(
                        value="0912 345 678",
                        source="facebook:profile_contact",
                        source_url="https://www.facebook.com/a.user/about",
                    ),
                ),
            ),
        ),
        issues=(),
        stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
    )


def test_pipeline_calls_provider_after_crawl_and_combines_two_slots() -> None:
    checked_at = datetime(2026, 8, 9, tzinfo=UTC)
    external = PhoneEvidence(
        phone_number="0987 654 321",
        normalized_phone="+84987654321",
        source="external:fbnumber",
        captured_at=checked_at,
        confidence="provider",
        provider="fbnumber",
    )
    provider = FakeProvider(
        ProviderResult(
            provider="fbnumber",
            status=ProviderStatus.FOUND,
            evidence=(external,),
            checked_at=checked_at,
        )
    )

    run = EnrichmentPipeline(provider).run_scrape_result(scrape_result())

    assert len(provider.identities) == 1
    assert provider.identities[0].uid == "10001"
    assert run.users[0].bundle.phone_1 == "+84987654321"
    assert run.users[0].bundle.phone_2 == "+84912345678"
    assert run.report.phone_1_found == 1
    assert run.report.phone_2_found == 1
    assert run.report.provider_found == 1
    assert run.users[0].bundle.profile.address == "Ha Noi"
    assert run.users[0].bundle.profile.birth_date == "12 thang 8, 1990"
    assert run.users[0].bundle.profile.gender == "Nam"


def test_provider_failure_keeps_crawler_phone_2() -> None:
    provider = FakeProvider(
        ProviderResult(
            provider="fbnumber",
            status=ProviderStatus.FAILED,
            error_code="provider_transport_error",
        )
    )

    run = EnrichmentPipeline(provider).run_scrape_result(scrape_result())

    assert run.users[0].bundle.phone_1 is None
    assert run.users[0].bundle.phone_2 == "+84912345678"
    assert run.report.provider_failed == 1
