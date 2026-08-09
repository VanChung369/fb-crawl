import csv
import json
from pathlib import Path

from fb_crawl.cli.app import main
from fb_crawl.exporters.schema import UNIFIED_FIELDS
from fb_crawl.exporters.phone_evidence import PHONE_EVIDENCE_FIELDS


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


def write_phone_evidence(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PHONE_EVIDENCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def phone_evidence_row(**values: str) -> dict[str, str]:
    result = {
        "user_id": "10001",
        "profile_url": "https://www.facebook.com/example.user",
        "phone_number": "0912 345 678",
        "source": "facebook:post_text",
        "source_url": "https://www.facebook.com/example.user/posts/1",
        "captured_at": "2026-08-09T01:00:00+00:00",
        "confidence": "strong_pattern",
    }
    result.update(values)
    return result


def test_phone_merge_cli_writes_master_and_quality_report(
    tmp_path: Path,
    capsys,
) -> None:
    first = tmp_path / "profile-phone-evidence.csv"
    second = tmp_path / "friends-phone-evidence.csv"
    output = tmp_path / "phone-evidence-master.csv"
    report = tmp_path / "phone-evidence-quality.json"
    write_phone_evidence(first, [phone_evidence_row()])
    write_phone_evidence(
        second,
        [
            phone_evidence_row(
                phone_number="+84 912-345-678",
                source="facebook:profile_contact",
                source_url=(
                    "https://www.facebook.com/example.user/directory_links"
                ),
                confidence="profile_field",
            )
        ],
    )

    exit_code = main(
        [
            "data",
            "phone-merge",
            str(tmp_path / "*-phone-evidence.csv"),
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
    assert rows[0]["user_id"] == "10001"
    assert rows[0]["normalized_phone"] == "+84912345678"
    assert rows[0]["evidence_count"] == "2"
    assert payload["records_written"] == 1
    assert payload["duplicates_merged"] == 1
    assert "records=1" in stdout
    assert "invalid_phone=0" in stdout


def test_phone_merge_cli_skips_wrong_csv_schema(tmp_path: Path) -> None:
    evidence_path = tmp_path / "evidence.csv"
    wrong_path = tmp_path / "users.csv"
    output = tmp_path / "master.csv"
    report = tmp_path / "report.json"
    write_phone_evidence(evidence_path, [phone_evidence_row()])
    write_rows(wrong_path, [base_row(user_id="10001")])

    assert main(
        [
            "data",
            "phone-merge",
            str(evidence_path),
            str(wrong_path),
            "--output",
            str(output),
            "--report",
            str(report),
        ]
    ) == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["input_files"] == 1
    assert payload["skipped_files"] == [str(wrong_path)]


def test_user_merge_does_not_treat_phone_evidence_as_user_rows(
    tmp_path: Path,
) -> None:
    users = tmp_path / "users.csv"
    evidence_path = tmp_path / "users-phone-evidence.csv"
    output = tmp_path / "users-master.csv"
    report = tmp_path / "quality.json"
    write_rows(
        users,
        [
            base_row(
                user_id="10001",
                name="Example User",
                profile_url="https://www.facebook.com/example.user",
            )
        ],
    )
    write_phone_evidence(evidence_path, [phone_evidence_row()])

    assert main(
        [
            "data",
            "merge",
            str(tmp_path / "*.csv"),
            "--output",
            str(output),
            "--report",
            str(report),
        ]
    ) == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["input_files"] == 1
    assert payload["skipped_files"] == [str(evidence_path)]
