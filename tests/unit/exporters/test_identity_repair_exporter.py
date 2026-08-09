import csv
from pathlib import Path

import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.models import IdentityRepairResult, IdentityRepairStats
from fb_crawl.exporters.identity_repair import (
    IDENTITY_FIELDS,
    read_identity_csv,
    write_identity_csv,
)


def test_reader_preserves_existing_columns_and_adds_identity_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "friends.csv"
    source.write_text(
        "user_id,name,username,profile_url,phone_numbers,custom\n"
        "123,174 friends,,https://www.facebook.com/profile.php?id=123,+84123,keep\n",
        encoding="utf-8",
    )

    fieldnames, rows = read_identity_csv(source)

    assert fieldnames == (
        "user_id",
        "name",
        "username",
        "profile_url",
        "phone_numbers",
        "custom",
        *IDENTITY_FIELDS,
    )
    assert rows[0]["phone_numbers"] == "+84123"
    assert rows[0]["custom"] == "keep"
    assert rows[0]["identity_status"] == ""


def test_reader_rejects_non_user_csv_before_browser_start(tmp_path: Path) -> None:
    source = tmp_path / "broken.csv"
    source.write_text("name,profile_url\nSynthetic User,/synthetic\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="required columns"):
        read_identity_csv(source)


def test_writer_replaces_destination_atomically_and_keeps_all_values(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "repaired.csv"
    destination.write_text("old\n", encoding="utf-8")
    fieldnames = ("user_id", "name", *IDENTITY_FIELDS)
    row = {field: "" for field in fieldnames}
    row.update(
        {
            "user_id": "123",
            "name": "Hiếu Văn",
            "identity_status": "repaired",
        }
    )
    result = IdentityRepairResult(
        fieldnames=fieldnames,
        rows=(row,),
        stats=IdentityRepairStats(
            rows=1,
            eligible=1,
            attempted=1,
            repaired=1,
            verified=0,
            failed=0,
            skipped=0,
            pending=0,
        ),
    )

    assert write_identity_csv(result, destination) is True

    with destination.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    assert rows[0]["name"] == "Hiếu Văn"
    assert rows[0]["identity_status"] == "repaired"
    assert not destination.with_name("repaired.csv.tmp").exists()
