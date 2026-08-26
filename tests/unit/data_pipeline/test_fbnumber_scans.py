from __future__ import annotations

from datetime import UTC, datetime

from fb_data_pipeline.importers.fbnumber_scans import (
    import_scan_item,
    parse_facebook_link,
    sync_scans_data_to_repository,
)


def test_parse_facebook_link_formats() -> None:
    # UID format with fb.com
    uid, username, canonical = parse_facebook_link("https://fb.com/100003795620679")
    assert uid == "100003795620679"
    assert username == ""
    assert "100003795620679" in canonical

    # UID format with profile.php
    uid2, username2, canonical2 = parse_facebook_link("https://www.facebook.com/profile.php?id=100000793630698")
    assert uid2 == "100000793630698"
    assert username2 == ""

    # Username format
    uid3, username3, canonical3 = parse_facebook_link("https://facebook.com/roaphan")
    assert uid3 == ""
    assert username3 == "roaphan"
    assert canonical3 == "https://www.facebook.com/roaphan"


def test_import_scan_item_full() -> None:
    sample = {
        "_id": "6a8da66491c69f3812eab32b",
        "birthday": "09/24",
        "gender": "Nữ",
        "linkFb": "https://fb.com/100003795620679",
        "location": "Ho Chi Minh City, Vietnam",
        "name": "Hồ Ngọc My Linh",
        "number": "0367131685",
        "number2": "0829462581",
        "number2Provider": "VinaPhone",
        "numberOfScans": 1,
        "numberProvider": "Viettel",
        "scanAt": "2026-08-25T14:27:48.166Z",
    }

    enriched = import_scan_item(sample)
    assert enriched is not None
    assert enriched.bundle.identity.uid == "100003795620679"
    assert enriched.bundle.identity.name == "Hồ Ngọc My Linh"
    assert enriched.bundle.profile.address == "Ho Chi Minh City, Vietnam"
    assert enriched.bundle.profile.birth_date == "09/24"
    assert enriched.bundle.profile.gender == "Nữ"

    # Evidence checks
    evidence = enriched.bundle.evidence
    assert len(evidence) == 2
    assert evidence[0].normalized_phone == "+84367131685"
    assert evidence[0].provider == "Viettel"
    assert evidence[1].normalized_phone == "+84829462581"
    assert evidence[1].provider == "VinaPhone"


def test_sync_scans_data_to_repository_mock() -> None:
    class MockRepo:
        def __init__(self):
            self.saved = []

        def save_enriched_user(self, enriched):
            self.saved.append(enriched)
            return len(self.saved)

    mock_repo = MockRepo()
    data = [
        {
            "_id": "1",
            "name": "User 1",
            "linkFb": "https://fb.com/100003795620679",
            "number": "0905123456",
            "scanAt": "2026-08-25T14:27:48.166Z",
        },
        {
            "_id": "2",
            "name": "User 2",
            "linkFb": "https://facebook.com/user2",
            "number": "0919408862",
            "scanAt": "2026-08-25T14:27:48.166Z",
        },
    ]

    result = sync_scans_data_to_repository(data, mock_repo)
    assert result.total_count == 2
    assert result.imported_count == 2
    assert result.skipped_count == 0
    assert len(mock_repo.saved) == 2
