from fb_crawl.core.models import (
    ContactKind,
    ContactRecord,
    PageRecord,
    PhoneEvidence as CrawlPhoneEvidence,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_data_pipeline.core.models import PhoneSlot
from fb_data_pipeline.importers.crawler import (
    import_page_record,
    import_scrape_result,
    import_user_record,
)


def test_user_record_maps_precise_crawler_evidence_to_phone_2() -> None:
    record = UserRecord(
        user_id="10001",
        username="a.user",
        name="A",
        profile_url="https://www.facebook.com/a.user",
        source="profile",
        source_url="https://www.facebook.com/a.user",
        phone_numbers=("0912 345 678",),
        phone_sources=("facebook:profile_contact",),
        phone_evidence=(
            CrawlPhoneEvidence(
                value="0912 345 678",
                source="facebook:profile_contact",
                source_url="https://www.facebook.com/a.user/about_contact_and_basic_info",
                captured_at="2026-08-09T01:02:03Z",
                confidence="profile_field",
            ),
        ),
    )

    bundle, invalid = import_user_record(record)

    assert invalid == 0
    assert bundle.identity.uid == "10001"
    assert bundle.identity.username == "a.user"
    assert len(bundle.evidence) == 1
    assert bundle.evidence[0].slot is PhoneSlot.PHONE_2
    assert bundle.phone_1 is None
    assert bundle.phone_2 == "+84912345678"
    assert bundle.evidence[0].captured_at is not None


def test_username_user_id_is_not_written_into_uid() -> None:
    record = UserRecord(
        user_id="a.user",
        name="A",
        profile_url="https://www.facebook.com/a.user",
        source="members",
        source_url="https://www.facebook.com/groups/example/members",
    )

    bundle, invalid = import_user_record(record)

    assert invalid == 0
    assert bundle.identity.uid == ""
    assert bundle.identity.username == "a.user"


def test_user_record_maps_raw_profile_attributes() -> None:
    record = UserRecord(
        user_id="100",
        name="A",
        profile_url="https://www.facebook.com/a.user",
        source="profile",
        source_url="https://www.facebook.com/a.user/about",
        address="Ha Noi",
        birth_date="12 thang 8, 1990",
        gender="Nam",
        last_enriched_at="2026-08-09T01:02:03Z",
    )

    bundle, invalid = import_user_record(record)

    assert invalid == 0
    assert bundle.profile.address == "Ha Noi"
    assert bundle.profile.birth_date == "12 thang 8, 1990"
    assert bundle.profile.gender == "Nam"
    assert bundle.profile.source_url == (
        "https://www.facebook.com/a.user/about"
    )
    assert bundle.profile.observed_at == datetime(
        2026,
        8,
        9,
        1,
        2,
        3,
        tzinfo=UTC,
    )


def test_user_record_fallback_phones_preserve_multiple_sources() -> None:
    record = UserRecord(
        user_id="10001",
        name="A",
        profile_url="https://www.facebook.com/a.user",
        source="profile",
        source_url="https://www.facebook.com/a.user",
        phone_numbers=("0912 345 678",),
        phone_sources=("facebook:intro", "facebook:post_text"),
        last_enriched_at="2026-08-09T01:02:03+00:00",
    )

    bundle, invalid = import_user_record(record)

    assert invalid == 0
    assert {item.source for item in bundle.evidence} == {
        "facebook:intro",
        "facebook:post_text",
    }
    assert all(item.slot is PhoneSlot.PHONE_2 for item in bundle.evidence)


def test_page_record_maps_public_phone_to_phone_2() -> None:
    record = PageRecord(
        canonical_url="https://m.facebook.com/example/?ref=page_internal",
        uid="20001",
        page_name="Example Page",
        address="Ho Chi Minh City",
        contacts=(
            ContactRecord(
                kind=ContactKind.PHONE,
                value="0987 654 321",
                sources=("facebook:page_phone",),
            ),
        ),
    )

    bundle, invalid = import_page_record(record)

    assert invalid == 0
    assert bundle.identity.uid == "20001"
    assert bundle.identity.profile_url == "https://www.facebook.com/example"
    assert bundle.phone_2 == "+84987654321"
    assert bundle.profile.address == "Ho Chi Minh City"
    assert bundle.profile.birth_date == ""
    assert bundle.profile.gender == ""
    assert bundle.profile.source_url == "https://www.facebook.com/example"


def test_scrape_result_import_reports_invalid_phone_without_losing_user() -> None:
    result = ScrapeResult(
        records=(
            UserRecord(
                user_id="10001",
                name="A",
                profile_url="https://www.facebook.com/a.user",
                source="profile",
                source_url="https://www.facebook.com/a.user",
                phone_numbers=("123",),
            ),
        ),
        issues=(),
        stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
    )

    imported = import_scrape_result(result)

    assert imported.records_read == 1
    assert imported.records_skipped == 0
    assert imported.invalid_phones == 1
    assert len(imported.bundles) == 1
    assert imported.bundles[0].evidence == ()
from datetime import UTC, datetime
