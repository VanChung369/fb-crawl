import csv
from pathlib import Path

from fb_crawl.core.models import (
    ContactKind,
    ContactRecord,
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeResult,
    ScrapeStats,
)
from fb_crawl.exporters.csv import write_csv
from fb_crawl.exporters.schema import UNIFIED_FIELDS


def test_empty_result_preserves_existing_csv(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "pages.csv"

    destination.write_text(
        "existing\n",
        encoding="utf-8",
    )

    result = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(
            requested=0,
            discovered=0,
            succeeded=0,
            failed=0,
        ),
    )

    assert (
        write_csv(
            result,
            destination,
        )
        is False
    )

    assert destination.read_text(encoding="utf-8") == "existing\n"


def test_csv_writes_success_and_issue_rows(
    tmp_path: Path,
) -> None:
    result = ScrapeResult(
        records=(
            PageRecord(
                canonical_url=("https://www.facebook.com/good"),
                page_name="Good",
                uid="100",
                category="Public figure",
                website="https://good.example",
                address="123 Example Street, Ha Noi",
                contacts=(
                    ContactRecord(
                        kind=ContactKind.PHONE,
                        value="+84 123 456 789",
                        sources=("facebook:profile_card",),
                    ),
                ),
            ),
        ),
        issues=(
            ScrapeIssue(
                code="public_fetch_failed",
                message="Public fetch failed.",
                target=("https://www.facebook.com/bad"),
                mode=ScrapeMode.PUBLIC,
                action=PublicAction.PAGE.value,
                retryable=True,
            ),
        ),
        stats=ScrapeStats(
            requested=2,
            discovered=0,
            succeeded=1,
            failed=1,
        ),
    )

    destination = tmp_path / "pages.csv"

    assert (
        write_csv(
            result,
            destination,
        )
        is True
    )

    with destination.open(encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    assert reader.fieldnames == list(UNIFIED_FIELDS)
    assert rows[0]["user_id"] == "100"
    assert rows[0]["name"] == "Good"
    assert rows[0]["username"] == "good"
    assert rows[0]["page_name"] == "Good"
    assert rows[0]["address"] == "123 Example Street, Ha Noi"
    assert rows[0]["phone_numbers"] == "+84 123 456 789"
    assert rows[0]["profile_url"] == "https://www.facebook.com/good"
    assert rows[0]["source_url"] == "https://www.facebook.com/good"
    assert rows[1]["source"] == PublicAction.PAGE.value
    assert rows[1]["source_url"] == "https://www.facebook.com/bad"
    assert rows[1]["error_code"] == "public_fetch_failed"
