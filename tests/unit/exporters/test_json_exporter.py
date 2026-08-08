import json
from pathlib import Path

from fb_crawl.core.models import (
    PageRecord,
    ScrapeResult,
    ScrapeStats,
)
from fb_crawl.exporters.json import write_json
from fb_crawl.exporters.schema import UNIFIED_FIELDS


def test_json_writes_full_result_envelope(
    tmp_path: Path,
) -> None:
    result = ScrapeResult(
        records=(
            PageRecord(
                canonical_url=("https://www.facebook.com/good"),
                page_name="Good",
                uid="100",
            ),
        ),
        issues=(),
        stats=ScrapeStats(
            requested=1,
            discovered=0,
            succeeded=1,
            failed=0,
        ),
    )

    destination = tmp_path / "pages.json"

    assert (
        write_json(
            result,
            destination,
        )
        is True
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))

    assert tuple(payload["records"][0]) == UNIFIED_FIELDS
    assert payload["records"][0]["user_id"] == "100"
    assert payload["records"][0]["name"] == "Good"
    assert payload["records"][0]["username"] == "good"
    assert payload["records"][0]["profile_url"].endswith("/good")

    assert payload["stats"]["succeeded"] == 1
    assert payload["enrichment"] is None
