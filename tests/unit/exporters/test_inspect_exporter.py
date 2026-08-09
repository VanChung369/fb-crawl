import csv
import json
from pathlib import Path

from fb_crawl.core.models import (
    InspectRecord,
    RetryStats,
    ScrapeResult,
    ScrapeStats,
)
from fb_crawl.exporters.authenticated import write_authenticated
from fb_crawl.exporters.inspect import INSPECT_FIELDS


def test_inspect_export_contains_only_diagnostic_schema(tmp_path: Path) -> None:
    result = ScrapeResult(
        records=(
            InspectRecord(
                target_url="https://www.facebook.com/synthetic.user",
                target_action="profile",
                session_valid=True,
                document_ready=True,
                main_found=True,
                dialog_count=0,
                visible_profile_links=4,
                message_rows=0,
                profile_field_labels=2,
            ),
        ),
        issues=(),
        stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
    )
    output = tmp_path / "inspect.csv"

    assert write_authenticated(result, output, "csv") is True
    with output.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    assert tuple(rows[0]) == INSPECT_FIELDS
    assert rows[0]["visible_profile_links"] == "4"
    assert "html" not in rows[0]
    assert "text" not in rows[0]


def test_inspect_json_includes_target_retry_coverage(tmp_path: Path) -> None:
    result = ScrapeResult(
        records=(
            InspectRecord(
                target_url="https://www.facebook.com/synthetic.user",
                target_action="profile",
                session_valid=True,
                document_ready=True,
                main_found=True,
                dialog_count=0,
                visible_profile_links=1,
                message_rows=0,
                profile_field_labels=0,
            ),
        ),
        issues=(),
        stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
        retry=RetryStats(
            attempted_targets=1,
            retried=0,
            rate_limited=0,
            pending=0,
        ),
    )
    output = tmp_path / "inspect.json"

    assert write_authenticated(result, output, "json") is True
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["retry"]["attempted_targets"] == 1
