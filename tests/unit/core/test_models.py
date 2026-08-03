import pytest

from fb_crawl.core.models import (
    ContactKind,
    ContactRecord,
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
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