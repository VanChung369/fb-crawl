import csv
import json
from pathlib import Path

from fb_crawl.cli.app import main
from fb_crawl.exporters.schema import UNIFIED_FIELDS


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=UNIFIED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def base_row(**values: str) -> dict[str, str]:
    result = {field: "" for field in UNIFIED_FIELDS}
    result.update(values)
    return result


def test_data_merge_cli_writes_atomic_master_and_quality_report(
    tmp_path: Path,
    capsys,
) -> None:
    first = tmp_path / "friends.csv"
    second = tmp_path / "members.csv"
    output = tmp_path / "users-master.csv"
    report = tmp_path / "quality-report.json"
    write_rows(
        first,
        [
            base_row(
                user_id="10001",
                name="Example User",
                profile_url="https://www.facebook.com/example.user",
                source="friends",
            )
        ],
    )
    write_rows(
        second,
        [
            base_row(
                user_id="10001",
                username="example.user",
                profile_url="https://www.facebook.com/profile.php?id=10001",
                phone_numbers="0912 345 678",
                phone_sources="facebook:profile_intro_text",
                source="members",
            )
        ],
    )

    exit_code = main(
        [
            "data",
            "merge",
            str(tmp_path / "*.csv"),
            "--output",
            str(output),
            "--report",
            str(report),
        ]
    )

    assert exit_code == 0
    with output.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    payload = json.loads(report.read_text(encoding="utf-8"))
    stdout = capsys.readouterr().out

    assert len(rows) == 1
    assert rows[0]["phone_numbers"] == "0912 345 678"
    assert payload["records_written"] == 1
    assert payload["coverage"]["phone_numbers"] == 1
    assert "records=1" in stdout
    assert "duplicates_merged=1" in stdout


def test_data_merge_skips_non_user_csv_and_reports_it(
    tmp_path: Path,
) -> None:
    users = tmp_path / "users.csv"
    messages = tmp_path / "messages.csv"
    output = tmp_path / "master.csv"
    report = tmp_path / "report.json"
    write_rows(
        users,
        [
            base_row(
                user_id="1",
                profile_url="https://www.facebook.com/example",
            )
        ],
    )
    messages.write_text("message_id,text\n1,hello\n", encoding="utf-8")

    assert main(
        [
            "data",
            "merge",
            str(users),
            str(messages),
            "--output",
            str(output),
            "--report",
            str(report),
        ]
    ) == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["input_files"] == 1
    assert payload["skipped_files"] == [str(messages)]
