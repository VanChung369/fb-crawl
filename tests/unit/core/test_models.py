import pytest

from fb_crawl.core.models import (
    AuthenticatedAction,
    ContactKind,
    ContactRecord,
    PageRecord,
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
