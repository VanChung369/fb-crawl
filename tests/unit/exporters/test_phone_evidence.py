import csv
from pathlib import Path

from fb_crawl.core.models import (
    PhoneEvidence,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.exporters.authenticated import write_authenticated
from fb_crawl.exporters.phone_evidence import (
    PHONE_EVIDENCE_FIELDS,
    write_phone_evidence,
)


def evidence_result() -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=(
            UserRecord(
                user_id="100013347102233",
                name="Synthetic User",
                profile_url=(
                    "https://www.facebook.com/profile.php?id=100013347102233"
                ),
                source="friends",
                source_url="https://www.facebook.com/example/friends",
                phone_evidence=(
                    PhoneEvidence(
                        value="+84 912 345 678",
                        source="facebook:post_text",
                        source_url="https://www.facebook.com/example/posts/1",
                        captured_at="2026-08-09T01:02:03+00:00",
                    ),
                    PhoneEvidence(
                        value="+84 912-345-678",
                        source="facebook:post_text",
                        source_url="https://www.facebook.com/example/posts/1",
                    ),
                    PhoneEvidence(
                        value="0912 345 678",
                        source="facebook:profile_contact",
                        source_url=(
                            "https://www.facebook.com/example/directory_links"
                        ),
                        confidence="profile_field",
                    ),
                ),
            ),
        ),
        issues=(),
        stats=ScrapeStats(
            requested=1,
            discovered=1,
            succeeded=1,
            failed=0,
        ),
    )


def test_phone_evidence_csv_is_atomic_typed_and_deduplicated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.csv"

    assert write_phone_evidence(evidence_result(), path) is True

    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    assert reader.fieldnames == list(PHONE_EVIDENCE_FIELDS)
    assert len(rows) == 2
    assert rows[0] == {
        "user_id": "100013347102233",
        "profile_url": (
            "https://www.facebook.com/profile.php?id=100013347102233"
        ),
        "phone_number": "+84 912 345 678",
        "source": "facebook:post_text",
        "source_url": "https://www.facebook.com/example/posts/1",
        "captured_at": "2026-08-09T01:02:03+00:00",
        "confidence": "strong_pattern",
    }
    assert rows[1]["phone_number"] == "0912 345 678"
    assert rows[1]["confidence"] == "profile_field"


def test_authenticated_export_always_uses_csv_evidence_sidecar(
    tmp_path: Path,
) -> None:
    path = tmp_path / "users.json"

    assert write_authenticated(evidence_result(), path, "json") is True

    evidence_path = tmp_path / "users-phone-evidence.csv"
    assert path.exists()
    assert evidence_path.exists()


def test_empty_evidence_preserves_existing_destination(tmp_path: Path) -> None:
    path = tmp_path / "evidence.csv"
    path.write_text("existing\n", encoding="utf-8")
    empty = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(0, 0, 0, 0),
    )

    assert write_phone_evidence(empty, path) is False
    assert path.read_text(encoding="utf-8") == "existing\n"
