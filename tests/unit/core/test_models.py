import pytest
from dataclasses import FrozenInstanceError

from fb_crawl.core.models import (
    AuthenticatedAction,
    ContactKind,
    ContactRecord,
    EnrichmentStats,
    PageRecord,
    ProfileDetails,
    ProfileField,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)


def test_scrape_request_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="limit must be greater than 0"):
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.PAGE,
            targets=("https://www.facebook.com/example",),
            limit=0,
        )


def test_result_reports_partial_failure_without_mutation() -> None:
    record = PageRecord(
        canonical_url="https://www.facebook.com/example",
        page_name="Example",
        contacts=(
            ContactRecord(
                kind=ContactKind.PHONE,
                value="+84 912 345 678",
                sources=("facebook:page_phone",),
            ),
        ),
    )

    issue = ScrapeIssue(
        code="public_fetch_failed",
        message="Public fetch failed.",
        target="https://www.facebook.com/bad",
        mode=ScrapeMode.PUBLIC,
        action=PublicAction.PAGE.value,
        retryable=True,
    )

    result = ScrapeResult(
        records=(record,),
        issues=(issue,),
        stats=ScrapeStats(
            requested=2,
            discovered=0,
            succeeded=1,
            failed=1,
        ),
    )

    assert result.has_failures is True
    assert result.records[0].contacts[0].value == "+84 912 345 678"


def test_authenticated_request_and_user_record_are_typed() -> None:
    request = ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.MEMBERS,
        targets=("https://www.facebook.com/groups/example/members",),
        steps=7,
    )

    record = UserRecord(
        user_id="123",
        name="Synthetic User",
        profile_url="https://www.facebook.com/profile.php?id=123",
        source="members",
        source_url=request.targets[0],
    )

    assert request.steps == 7
    assert record.user_id == "123"


def test_request_rejects_non_positive_authenticated_steps() -> None:
    with pytest.raises(ValueError, match="steps must be greater than 0"):
        ScrapeRequest(
            mode=ScrapeMode.AUTHENTICATED,
            action=AuthenticatedAction.COMMENTS,
            targets=("https://www.facebook.com/example/posts/1",),
            steps=0,
        )


def test_profile_enrichment_contracts_are_typed_and_immutable() -> None:
    request = ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.MEMBERS,
        targets=("https://www.facebook.com/groups/example/members",),
        enrich_profiles=True,
        profile_fields=(ProfileField.PHONE, ProfileField.BIRTH_DATE),
        profile_limit=7,
        profile_delay_seconds=1.5,
    )
    details = ProfileDetails(
        phone_numbers=("+84 123 456 789",),
        birth_date="1990-01-02",
        birth_year=1990,
    )
    stats = EnrichmentStats(
        selected=1,
        attempted=1,
        succeeded=1,
        failed=0,
        phone_found=1,
        address_found=0,
        current_city_found=0,
        hometown_found=0,
        birth_year_found=1,
    )

    assert request.profile_fields == (ProfileField.PHONE, ProfileField.BIRTH_DATE)
    assert details.birth_year == 1990
    assert stats.phone_found == 1

    with pytest.raises(FrozenInstanceError):
        details.birth_year = 1991  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile_fields": (ProfileField.PHONE,)},
        {"enrich_profiles": True, "profile_limit": 0},
        {"enrich_profiles": True, "profile_delay_seconds": -0.1},
        {
            "enrich_profiles": True,
            "profile_fields": (ProfileField.PHONE, ProfileField.PHONE),
        },
        {"enrich_profiles": True, "profile_fields": ("phone",)},
    ],
)
def test_request_rejects_invalid_profile_enrichment_options(overrides) -> None:
    with pytest.raises(ValueError):
        ScrapeRequest(
            mode=ScrapeMode.AUTHENTICATED,
            action=AuthenticatedAction.MEMBERS,
            targets=("https://www.facebook.com/groups/example/members",),
            **overrides,
        )


def test_existing_user_record_constructor_has_empty_enrichment_defaults() -> None:
    record = UserRecord(
        user_id="123",
        name="Synthetic User",
        profile_url="https://www.facebook.com/profile.php?id=123",
        source="members",
        source_url="https://www.facebook.com/groups/example/members",
    )

    assert record.phone_numbers == ()
    assert record.current_city is None
    assert record.birth_year is None
