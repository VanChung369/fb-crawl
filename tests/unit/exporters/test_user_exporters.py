import pytest
import csv
import json
from dataclasses import replace
from pathlib import Path

from fb_crawl.core.models import (
    AuthenticatedAction,
    EnrichmentStats,
    ScrapeIssue,
    RetryStats,
    ScrapeMode,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.exporters.users import write_users
from fb_crawl.exporters.schema import UNIFIED_FIELDS
from fb_crawl.exporters.schema import user_record_row
from fb_crawl.core.exceptions import (
    ConfigurationError,
    ExportError,
)


def result() -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=(
            UserRecord(
                user_id="synthetic.user",
                name="Synthetic User",
                profile_url="https://www.facebook.com/synthetic.user",
                source="members",
                source_url="https://www.facebook.com/groups/1/members",
                phone_numbers=("+84 123 456 789",),
                phone_sources=("facebook:profile_contact",),
                website="https://profile.example.test",
                address="123 Synthetic Street",
                current_city="Synthetic City",
                hometown="Synthetic Province",
                birth_date="1990-01-02",
                birth_year=1990,
            ),
            UserRecord(
                user_id="synthetic.user",
                name="Duplicate",
                profile_url="https://www.facebook.com/synthetic.user",
                source="comments",
                source_url="https://www.facebook.com/acme/posts/1",
            ),
        ),
        issues=(
            ScrapeIssue(
                code="authenticated_navigation_failed",
                message="Authenticated comments navigation failed.",
                target="https://www.facebook.com/acme/posts/2",
                mode=ScrapeMode.AUTHENTICATED,
                action=AuthenticatedAction.COMMENTS.value,
            ),
        ),
        stats=ScrapeStats(
            requested=3,
            discovered=2,
            succeeded=1,
            failed=1,
        ),
        enrichment=EnrichmentStats(
            selected=1,
            attempted=1,
            succeeded=1,
            failed=0,
            phone_found=1,
            address_found=1,
            current_city_found=1,
            hometown_found=1,
            birth_year_found=1,
        ),
    )


def test_user_csv_deduplicates_and_appends_issue_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "users.csv"

    assert write_users(result(), path, "csv") is True

    with path.open(encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    assert reader.fieldnames == list(UNIFIED_FIELDS)
    assert [row["user_id"] for row in rows] == ["", ""]
    assert rows[0]["username"] == "synthetic.user"
    assert rows[0]["page_name"] == ""
    assert rows[0]["address"] == "123 Synthetic Street"
    assert rows[0]["current_city"] == "Synthetic City"
    assert rows[0]["hometown"] == "Synthetic Province"
    assert rows[0]["birth_date"] == "1990-01-02"
    assert rows[0]["birth_year"] == "1990"
    assert rows[0]["depth"] == "0"
    assert rows[0]["phone_numbers"] == "+84 123 456 789"
    assert rows[1]["error_code"] == "authenticated_navigation_failed"


def test_user_json_keeps_full_result_envelope(tmp_path: Path) -> None:
    path = tmp_path / "users.json"

    assert write_users(result(), path, "json") is True

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert tuple(payload["records"][0]) == UNIFIED_FIELDS
    assert payload["records"][0]["user_id"] == ""
    assert payload["records"][0]["username"] == "synthetic.user"
    assert len(payload["records"]) == 1
    assert payload["stats"]["failed"] == 1
    assert payload["enrichment"]["birth_year_found"] == 1


def test_user_json_includes_authenticated_retry_coverage(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    retry_result = replace(
        result(),
        retry=RetryStats(
            attempted_targets=2,
            retried=1,
            rate_limited=1,
            pending=0,
        ),
    )

    assert write_users(retry_result, path, "json") is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["retry"] == {
        "attempted_targets": 2,
        "retried": 1,
        "rate_limited": 1,
        "pending": 0,
        "interrupted": 0,
    }


def test_numeric_profile_php_is_not_exported_as_username() -> None:
    record = UserRecord(
        user_id="123",
        name="Synthetic User",
        profile_url="https://www.facebook.com/profile.php?id=123",
        source="members",
        source_url="https://www.facebook.com/groups/1/members",
    )

    assert user_record_row(record)["username"] == ""


def test_user_txt_writes_records_and_target_issues(
    tmp_path: Path,
) -> None:
    path = tmp_path / "users.txt"

    assert write_users(result(), path, "txt") is True

    content = path.read_text(encoding="utf-8")

    assert "User ID: \n" in content
    assert "Username: synthetic.user" in content
    assert "Depth: 0" in content
    assert "Phone Numbers: +84 123 456 789" in content
    assert "Address: 123 Synthetic Street" in content
    assert "Current City: Synthetic City" in content
    assert "Hometown: Synthetic Province" in content
    assert "Birth Date: 1990-01-02" in content
    assert "Birth Year: 1990" in content
    assert "Error: [authenticated_navigation_failed]" in content


def test_empty_user_result_preserves_existing_destination(
    tmp_path: Path,
) -> None:
    path = tmp_path / "users.csv"
    path.write_text("existing\n", encoding="utf-8")

    empty = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(
            requested=0,
            discovered=0,
            succeeded=0,
            failed=0,
        ),
    )

    assert write_users(empty, path, "csv") is False
    assert path.read_text(encoding="utf-8") == "existing\n"


def test_user_xlsx_uses_the_same_schema(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    path = tmp_path / "users.xlsx"

    assert write_users(result(), path, "xlsx") is True

    rows = list(load_workbook(path).active.values)

    assert rows[0] == UNIFIED_FIELDS
    assert rows[1][0] is None
    assert rows[1][2] == "synthetic.user"
    assert rows[2][UNIFIED_FIELDS.index("error_code")] == (
        "authenticated_navigation_failed"
    )


def test_xlsx_missing_dependency_does_not_change_format(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "fb_crawl.exporters.users._workbook_type",
        lambda: (_ for _ in ()).throw(ConfigurationError("XLSX extra required.")),
    )

    path = tmp_path / "users.xlsx"

    with pytest.raises(ConfigurationError, match="XLSX"):
        write_users(result(), path, "xlsx")

    assert not path.exists()
    assert not (tmp_path / "users.csv").exists()


def test_failed_xlsx_save_preserves_existing_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "users.xlsx"
    path.write_bytes(b"existing")

    class BrokenWorkbook:
        active = type(
            "Sheet",
            (),
            {
                "title": "",
                "append": lambda self, row: None,
            },
        )()

        def save(self, temporary: Path) -> None:
            temporary.write_bytes(b"partial")
            raise OSError("disk failure")

    monkeypatch.setattr(
        "fb_crawl.exporters.users._workbook_type",
        lambda: BrokenWorkbook,
    )

    with pytest.raises(ExportError):
        write_users(result(), path, "xlsx")

    assert path.read_bytes() == b"existing"
    assert not path.with_name("users.xlsx.tmp.xlsx").exists()
