import csv
from pathlib import Path

from fb_crawl.core.models import (
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeResult,
    ScrapeStats,
)
from fb_crawl.exporters.csv import write_csv


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
        rows = list(csv.DictReader(file))

    assert rows[0]["page_name"] == "Good"
    assert rows[1]["error_code"] == "public_fetch_failed"
