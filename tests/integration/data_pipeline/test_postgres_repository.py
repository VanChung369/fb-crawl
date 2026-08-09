from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from fb_crawl.core.models import ScrapeResult, ScrapeStats, UserRecord
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProfileData,
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict
from fb_data_pipeline.repositories.migrations import MigrationRunner
from fb_data_pipeline.repositories.postgres import PostgresRepository
from fb_data_pipeline.services.ingestion import AuthenticatedIngestionService
from fb_data_pipeline.services.persistence import PipelinePersistenceService
from fb_data_pipeline.services.pipeline import EnrichedUser, EnrichmentPipeline
from fb_data_pipeline.services.retry import FBNumberRetryService


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured",
)


def _assert_dedicated_test_database() -> None:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        if not connection.info.dbname.endswith("_test"):
            pytest.fail(
                "TEST_DATABASE_URL must target a database ending in _test"
            )


@pytest.fixture(autouse=True)
def clean_database() -> None:
    _assert_dedicated_test_database()
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    enrichment_attempts,
                    user_phone_evidence,
                    facebook_user_profiles,
                    phone_numbers,
                    facebook_users
                RESTART IDENTITY
                """
            )


def evidence(
    phone: str,
    *,
    source: str,
    captured_at: datetime,
    confidence: str,
    provider: str = "",
) -> PhoneEvidence:
    return PhoneEvidence(
        phone_number=phone,
        normalized_phone=phone,
        source=source,
        captured_at=captured_at,
        confidence=confidence,
        provider=provider,
    )


def enriched(
    identity: FacebookIdentity,
    *,
    evidence_items: tuple[PhoneEvidence, ...] = (),
    status: ProviderStatus = ProviderStatus.FOUND,
    profile: ProfileData | None = None,
    checked_at: datetime | None = None,
) -> EnrichedUser:
    provider_evidence = tuple(
        item for item in evidence_items if item.provider == "fbnumber"
    )
    return EnrichedUser(
        bundle=UserBundle(
            identity=identity,
            evidence=evidence_items,
            profile=profile or ProfileData(),
        ),
        provider_result=ProviderResult(
            provider="fbnumber",
            status=status,
            evidence=provider_evidence,
            checked_at=checked_at or datetime(2026, 8, 9, tzinfo=UTC),
            error_code=("provider_failed" if status is ProviderStatus.FAILED else ""),
        ),
    )


def scalar(sql: str) -> int:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def test_migrations_are_idempotent_on_postgresql() -> None:
    assert MigrationRunner(TEST_DATABASE_URL).apply() == ()
    assert MigrationRunner(TEST_DATABASE_URL).apply() == ()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
            assert cursor.fetchall() == [
                ("001_initial",),
                ("002_profile_attributes",),
            ]


def test_reprocessing_user_deduplicates_data_and_counts_evidence() -> None:
    captured_at = datetime(2026, 8, 9, tzinfo=UTC)
    item = enriched(
        FacebookIdentity(uid="100", username="sample.user"),
        evidence_items=(
            evidence(
                "+84911111111",
                source="profile_about",
                captured_at=captured_at,
                confidence="profile_field",
            ),
            evidence(
                "+84922222222",
                source="external:fbnumber",
                captured_at=captured_at,
                confidence="provider_result",
                provider="fbnumber",
            ),
        ),
    )
    repository = PostgresRepository(TEST_DATABASE_URL)

    first_id = repository.save_enriched_user(item)
    second_id = repository.save_enriched_user(item)

    assert second_id == first_id
    assert scalar("SELECT count(*) FROM facebook_users") == 1
    assert scalar("SELECT count(*) FROM phone_numbers") == 2
    assert scalar("SELECT count(*) FROM user_phone_evidence") == 2
    assert scalar(
        "SELECT min(evidence_count) FROM user_phone_evidence"
    ) == 2
    assert scalar("SELECT count(*) FROM enrichment_attempts") == 2


def test_later_aliases_enrich_the_same_user() -> None:
    repository = PostgresRepository(TEST_DATABASE_URL)
    first_id = repository.save_enriched_user(
        enriched(FacebookIdentity(uid="100"), status=ProviderStatus.NOT_FOUND)
    )

    second_id = repository.save_enriched_user(
        enriched(
            FacebookIdentity(
                uid="100",
                username="sample.user",
                profile_url="https://www.facebook.com/sample.user",
            ),
            status=ProviderStatus.NOT_FOUND,
        )
    )

    assert second_id == first_id
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT normalized_username, profile_url
                FROM facebook_users
                WHERE id = %s
                """,
                (first_id,),
            )
            assert cursor.fetchone() == (
                "sample.user",
                "https://www.facebook.com/sample.user",
            )


def test_conflicting_aliases_roll_back_current_user() -> None:
    repository = PostgresRepository(TEST_DATABASE_URL)
    repository.save_enriched_user(
        enriched(
            FacebookIdentity(uid="100", username="first.user"),
            status=ProviderStatus.NOT_FOUND,
        )
    )
    repository.save_enriched_user(
        enriched(
            FacebookIdentity(uid="200", username="second.user"),
            status=ProviderStatus.NOT_FOUND,
        )
    )
    attempts_before = scalar("SELECT count(*) FROM enrichment_attempts")

    with pytest.raises(DatabaseIdentityConflict):
        repository.save_enriched_user(
            enriched(
                FacebookIdentity(uid="100", username="second.user"),
                status=ProviderStatus.NOT_FOUND,
            )
        )

    assert scalar("SELECT count(*) FROM facebook_users") == 2
    assert scalar("SELECT count(*) FROM enrichment_attempts") == attempts_before


def test_view_selects_newest_provider_and_strongest_crawler_phone() -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    item = enriched(
        FacebookIdentity(uid="100"),
        evidence_items=(
            evidence(
                "+84911111111",
                source="external:fbnumber:old",
                captured_at=now,
                confidence="provider_result",
                provider="fbnumber",
            ),
            evidence(
                "+84922222222",
                source="external:fbnumber:new",
                captured_at=now + timedelta(minutes=5),
                confidence="provider_result",
                provider="fbnumber",
            ),
            evidence(
                "+84933333333",
                source="post_pattern",
                captured_at=now + timedelta(minutes=10),
                confidence="weak_pattern",
            ),
            evidence(
                "+84944444444",
                source="profile_about",
                captured_at=now,
                confidence="profile_field",
            ),
        ),
    )

    PostgresRepository(TEST_DATABASE_URL).save_enriched_user(item)

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT phone_1, phone_2 FROM facebook_user_phone_slots"
            )
            assert cursor.fetchone() == (
                "+84922222222",
                "+84944444444",
            )


@pytest.mark.parametrize(
    "status",
    [
        ProviderStatus.NOT_FOUND,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.FAILED,
    ],
)
def test_provider_failure_statuses_keep_crawler_phone(
    status: ProviderStatus,
) -> None:
    crawler = evidence(
        "+84911111111",
        source="profile_about",
        captured_at=datetime(2026, 8, 9, tzinfo=UTC),
        confidence="profile_field",
    )

    PostgresRepository(TEST_DATABASE_URL).save_enriched_user(
        enriched(
            FacebookIdentity(uid="100"),
            evidence_items=(crawler,),
            status=status,
        )
    )

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT phone_1, phone_2 FROM facebook_user_phone_slots"
            )
            assert cursor.fetchone() == (None, "+84911111111")
            cursor.execute("SELECT status FROM enrichment_attempts")
            assert cursor.fetchone() == (status.value,)


def test_profile_view_prefers_newer_values_and_preserves_blank_fields() -> None:
    repository = PostgresRepository(TEST_DATABASE_URL)
    earlier = datetime(2026, 8, 8, tzinfo=UTC)
    later = datetime(2026, 8, 9, tzinfo=UTC)
    stale = datetime(2026, 8, 7, tzinfo=UTC)
    identity = FacebookIdentity(uid="100", username="sample.user")

    repository.save_enriched_user(
        enriched(
            identity,
            status=ProviderStatus.NOT_FOUND,
            profile=ProfileData(
                address="Ha Noi",
                birth_date="1990",
                gender="Nam",
                source_url="https://www.facebook.com/sample.user/about",
                observed_at=earlier,
            ),
        )
    )
    repository.save_enriched_user(
        enriched(
            identity,
            status=ProviderStatus.NOT_FOUND,
            profile=ProfileData(
                address="Da Nang",
                source_url="https://www.facebook.com/sample.user/about",
                observed_at=later,
            ),
        )
    )
    repository.save_enriched_user(
        enriched(
            identity,
            status=ProviderStatus.NOT_FOUND,
            profile=ProfileData(
                address="Stale address",
                birth_date="Stale birth date",
                gender="Stale gender",
                source_url="https://www.facebook.com/sample.user/about",
                observed_at=stale,
            ),
        )
    )

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT address, birth_date, gender
                FROM facebook_user_phone_slots
                """
            )
            assert cursor.fetchone() == ("Da Nang", "1990", "Nam")


def test_deleting_user_cascades_to_profile_snapshot() -> None:
    repository = PostgresRepository(TEST_DATABASE_URL)
    user_id = repository.save_enriched_user(
        enriched(
            FacebookIdentity(uid="100"),
            status=ProviderStatus.NOT_FOUND,
            profile=ProfileData(address="Ha Noi"),
        )
    )

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM facebook_users WHERE id = %s",
                (user_id,),
            )

    assert scalar("SELECT count(*) FROM facebook_user_profiles") == 0


def test_in_memory_crawl_enrichment_persists_authoritative_view() -> None:
    captured_at = datetime(2026, 8, 9, 6, 0, tzinfo=UTC)

    class StaticProvider:
        def search(self, identity: FacebookIdentity) -> ProviderResult:
            assert identity.uid == "100"
            return ProviderResult(
                provider="fbnumber",
                status=ProviderStatus.FOUND,
                evidence=(
                    PhoneEvidence(
                        phone_number="0987654321",
                        normalized_phone="+84987654321",
                        source="external:fbnumber",
                        captured_at=captured_at,
                        confidence="provider",
                        provider="fbnumber",
                    ),
                ),
                checked_at=captured_at,
            )

    result = ScrapeResult(
        records=(
            UserRecord(
                user_id="100",
                name="Synthetic User",
                username="synthetic.user",
                profile_url="https://www.facebook.com/synthetic.user",
                source="friends",
                source_url="https://www.facebook.com/source/friends",
                phone_numbers=("0912345678",),
                phone_sources=("profile_about",),
                address="Ha Noi",
                birth_date="12 thang 8, 1990",
                gender="Nam",
                last_enriched_at=captured_at.isoformat(),
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
    service = AuthenticatedIngestionService(
        EnrichmentPipeline(StaticProvider()),
        PipelinePersistenceService(PostgresRepository(TEST_DATABASE_URL)),
    )

    report = service.ingest(result)

    assert report.persistence.persisted == 1
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    facebook_uid,
                    facebook_username,
                    phone_1,
                    phone_2,
                    address,
                    birth_date,
                    gender
                FROM facebook_user_phone_slots
                """
            )
            assert cursor.fetchone() == (
                "100",
                "synthetic.user",
                "+84987654321",
                "+84912345678",
                "Ha Noi",
                "12 thang 8, 1990",
                "Nam",
            )


def test_latest_terminal_attempt_suppresses_older_retryable_attempt() -> None:
    repository = PostgresRepository(TEST_DATABASE_URL)
    identity = FacebookIdentity(uid="100", username="sample.user")
    timestamp = datetime(2026, 8, 8, tzinfo=UTC)
    repository.save_enriched_user(
        enriched(
            identity,
            status=ProviderStatus.FAILED,
            checked_at=timestamp,
        )
    )
    repository.save_enriched_user(
        enriched(
            identity,
            status=ProviderStatus.NOT_FOUND,
            checked_at=timestamp,
        )
    )

    assert repository.list_fbnumber_retry_candidates(
        eligible_before=datetime(2026, 8, 9, tzinfo=UTC),
        limit=20,
    ) == ()


def test_retry_candidate_selection_honors_cooldown_force_and_identity() -> None:
    repository = PostgresRepository(TEST_DATABASE_URL)
    checked_at = datetime(2026, 8, 9, 11, 0, tzinfo=UTC)
    repository.save_enriched_user(
        enriched(
            FacebookIdentity(uid="100", username="sample.user"),
            status=ProviderStatus.RATE_LIMITED,
            checked_at=checked_at,
        )
    )
    repository.save_enriched_user(
        enriched(
            FacebookIdentity(
                profile_url="https://www.facebook.com/profile.only"
            ),
            status=ProviderStatus.FAILED,
            checked_at=datetime(2026, 8, 7, tzinfo=UTC),
        )
    )

    assert repository.list_fbnumber_retry_candidates(
        eligible_before=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        limit=20,
    ) == ()
    forced = repository.list_fbnumber_retry_candidates(
        eligible_before=None,
        limit=20,
    )
    assert len(forced) == 1
    assert forced[0].identity.uid == "100"
    assert forced[0].status is ProviderStatus.RATE_LIMITED


def test_fbnumber_retry_advisory_lock_serializes_workers() -> None:
    first = PostgresRepository(TEST_DATABASE_URL)
    second = PostgresRepository(TEST_DATABASE_URL)

    with first.fbnumber_retry_lock() as first_acquired:
        assert first_acquired is True
        with second.fbnumber_retry_lock() as second_acquired:
            assert second_acquired is False

    with second.fbnumber_retry_lock() as acquired_after_release:
        assert acquired_after_release is True


def test_successful_retry_persists_phone_and_preserves_crawler_data() -> None:
    earlier = datetime(2026, 8, 7, tzinfo=UTC)
    now = datetime(2026, 8, 9, tzinfo=UTC)
    identity = FacebookIdentity(uid="100", username="sample.user")
    crawler = evidence(
        "+84911111111",
        source="profile_about",
        captured_at=earlier,
        confidence="profile_field",
    )
    repository = PostgresRepository(TEST_DATABASE_URL)
    repository.save_enriched_user(
        enriched(
            identity,
            evidence_items=(crawler,),
            status=ProviderStatus.FAILED,
            profile=ProfileData(
                address="Ha Noi",
                birth_date="1990",
                gender="Nam",
                observed_at=earlier,
            ),
            checked_at=earlier,
        )
    )

    class StaticProvider:
        def search(self, received: FacebookIdentity) -> ProviderResult:
            assert received == identity
            phone = evidence(
                "+84922222222",
                source="external:fbnumber",
                captured_at=now,
                confidence="provider_result",
                provider="fbnumber",
            )
            return ProviderResult(
                provider="fbnumber",
                status=ProviderStatus.FOUND,
                evidence=(phone,),
                checked_at=now,
            )

    report = FBNumberRetryService(
        repository,
        EnrichmentPipeline(StaticProvider()),
        PipelinePersistenceService(repository),
        clock=lambda: now,
    ).run()

    assert report.selected == 1
    assert report.persisted == 1
    assert report.found == 1
    assert report.retry_pending == 0
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT phone_1, phone_2, address, birth_date, gender
                FROM facebook_user_phone_slots
                """
            )
            assert cursor.fetchone() == (
                "+84922222222",
                "+84911111111",
                "Ha Noi",
                "1990",
                "Nam",
            )
            cursor.execute(
                """
                SELECT status
                FROM enrichment_attempts
                ORDER BY checked_at DESC, id DESC
                LIMIT 1
                """
            )
            assert cursor.fetchone() == ("found",)


def test_repeated_failure_starts_a_new_cooldown() -> None:
    earlier = datetime(2026, 8, 7, tzinfo=UTC)
    now = datetime(2026, 8, 9, tzinfo=UTC)
    repository = PostgresRepository(TEST_DATABASE_URL)
    repository.save_enriched_user(
        enriched(
            FacebookIdentity(uid="100", username="sample.user"),
            status=ProviderStatus.FAILED,
            checked_at=earlier,
        )
    )

    class FailingProvider:
        def search(self, _identity: FacebookIdentity) -> ProviderResult:
            return ProviderResult(
                provider="fbnumber",
                status=ProviderStatus.FAILED,
                checked_at=now,
                error_code="provider_transport_error",
            )

    report = FBNumberRetryService(
        repository,
        EnrichmentPipeline(FailingProvider()),
        PipelinePersistenceService(repository),
        clock=lambda: now,
    ).run()

    assert report.failed == 1
    assert report.retry_pending == 1
    assert repository.list_fbnumber_retry_candidates(
        eligible_before=now - timedelta(hours=24),
        limit=20,
    ) == ()
    forced = repository.list_fbnumber_retry_candidates(
        eligible_before=None,
        limit=20,
    )
    assert len(forced) == 1
    assert forced[0].checked_at == now
