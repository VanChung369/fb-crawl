import csv
import json
from pathlib import Path

from fb_crawl.cli.app import build_parser, main
from fb_crawl.cli.authenticated import request_from_authenticated_args
from fb_crawl.exporters.schema import UNIFIED_FIELDS


def write_master(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=UNIFIED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def master_row(**values: str) -> dict[str, str]:
    row = {field: "" for field in UNIFIED_FIELDS}
    row.update(
        {
            "user_id": "10001",
            "name": "Example User",
            "username": "example.user",
            "profile_url": "https://www.facebook.com/example.user",
        }
    )
    row.update(values)
    return row


def test_data_plan_cli_writes_batch_compatible_targets_and_report(
    tmp_path: Path,
    capsys,
) -> None:
    master = tmp_path / "users-master.csv"
    output = tmp_path / "enrichment.txt"
    report = tmp_path / "enrichment-plan.json"
    write_master(
        master,
        [
            master_row(),
            master_row(
                user_id="2",
                username="complete.user",
                profile_url="https://www.facebook.com/complete.user",
                phone_numbers="0912 345 678",
                address="Known",
            ),
        ],
    )

    exit_code = main(
        [
            "data",
            "plan",
            str(master),
            "--missing",
            "phone,address",
            "--cooldown-days",
            "30",
            "--failure-cooldown-days",
            "2",
            "--limit",
            "10",
            "--output",
            str(output),
            "--report",
            str(report),
        ]
    )

    assert exit_code == 0
    assert output.read_text(encoding="utf-8").splitlines() == [
        "profile:https://www.facebook.com/example.user"
    ]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["selected"] == 1
    assert payload["skipped_complete"] == 1
    assert payload["profile_fields"] == ["phone", "address"]
    assert payload["failure_cooldown_days"] == 2
    assert "selected=1" in capsys.readouterr().out

    batch_args = build_parser().parse_args(
        [
            "authenticated",
            "batch",
            "--input",
            str(output),
            "--enrich-profiles",
            "--profile-fields",
            "phone,address",
        ]
    )
    request = request_from_authenticated_args(batch_args)
    assert request.targets == (
        "profile:https://www.facebook.com/example.user",
    )


def test_data_plan_cli_rejects_invalid_policy_before_writing(
    tmp_path: Path,
) -> None:
    master = tmp_path / "users-master.csv"
    output = tmp_path / "targets.txt"
    write_master(master, [master_row()])

    assert main(
        [
            "data",
            "plan",
            str(master),
            "--missing",
            "unknown",
            "--output",
            str(output),
        ]
    ) == 2
    assert not output.exists()
