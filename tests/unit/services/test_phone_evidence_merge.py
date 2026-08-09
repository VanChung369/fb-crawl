import pytest

from fb_crawl.services.phone_evidence_merge import (
    PhoneEvidenceMergeService,
    SourcePhoneEvidence,
    normalize_phone,
)


def evidence(**values: str) -> SourcePhoneEvidence:
    defaults = {
        "source_file": "profile-phone-evidence.csv",
        "line_number": 2,
        "user_id": "10001",
        "profile_url": "https://www.facebook.com/profile.php?id=10001",
        "phone_number": "0912 345 678",
        "source": "facebook:post_text",
        "source_url": "https://www.facebook.com/example/posts/1",
        "captured_at": "2026-08-09T01:00:00+00:00",
        "confidence": "strong_pattern",
    }
    defaults.update(values)
    return SourcePhoneEvidence(**defaults)


def test_phone_normalization_collapses_vietnam_prefix_forms() -> None:
    expected = "+84912345678"

    assert normalize_phone("0912 345 678", "84") == expected
    assert normalize_phone("84 912 345 678", "84") == expected
    assert normalize_phone("+84 (912) 345-678", "84") == expected
    assert normalize_phone("0084 912 345 678", "84") == expected
    assert normalize_phone("123", "84") is None
    assert normalize_phone("0000000000", "84") is None

    with pytest.raises(ValueError, match="country code"):
        normalize_phone("0912 345 678", "84abc")


def test_merge_keeps_all_provenance_and_best_identity() -> None:
    result = PhoneEvidenceMergeService().run(
        (
            evidence(),
            evidence(
                line_number=3,
                profile_url="https://www.facebook.com/example",
                phone_number="+84 912-345-678",
                source="facebook:profile_contact",
                source_url=(
                    "https://www.facebook.com/example/directory_links"
                ),
                captured_at="2026-08-09T03:00:00Z",
                confidence="profile_field",
            ),
            evidence(
                line_number=4,
                profile_url="https://www.facebook.com/example",
                phone_number="84912345678",
            ),
        ),
        input_files=1,
        default_country_code="84",
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.user_id == "10001"
    assert row.profile_url == "https://www.facebook.com/example"
    assert row.phone_number == "0912 345 678"
    assert row.normalized_phone == "+84912345678"
    assert row.sources == (
        "facebook:post_text",
        "facebook:profile_contact",
    )
    assert row.source_urls == (
        "https://www.facebook.com/example/posts/1",
        "https://www.facebook.com/example/directory_links",
    )
    assert row.first_captured_at == "2026-08-09T01:00:00+00:00"
    assert row.last_captured_at == "2026-08-09T03:00:00+00:00"
    assert row.confidence == "profile_field"
    assert row.evidence_count == 3
    assert row.quality_status == ("ok",)
    assert result.report.duplicates_merged == 2
    assert result.report.coverage["numeric_uid"] == 1


def test_merge_reports_invalid_or_incomplete_evidence_without_hiding_it() -> None:
    result = PhoneEvidenceMergeService().run(
        (
            evidence(
                user_id="",
                profile_url="https://www.facebook.com/no.uid",
                source_url="https://example.test/not-facebook",
                captured_at="not-a-time",
            ),
            evidence(
                line_number=3,
                user_id="10001",
                profile_url="https://www.facebook.com/profile.php?id=20002",
            ),
            evidence(line_number=4, phone_number="123"),
        ),
        input_files=1,
        default_country_code="84",
    )

    assert len(result.rows) == 2
    assert result.rows[0].quality_status == (
        "missing_uid",
        "invalid_captured_at",
        "invalid_source_url",
    )
    assert result.rows[1].quality_status == ("identity_conflict",)
    assert result.report.invalid_phone_rows == 1
    assert result.report.missing_uid_rows == 1
    assert result.report.invalid_source_url_rows == 1
    assert result.report.invalid_timestamp_rows == 1
    assert result.report.identity_conflict_rows == 1
    assert [issue.code for issue in result.issues] == [
        "invalid_captured_at",
        "invalid_source_url",
        "identity_conflict",
        "invalid_phone",
    ]


def test_missing_uid_is_recovered_from_a_numeric_profile_url() -> None:
    result = PhoneEvidenceMergeService().run(
        (evidence(user_id=""),),
        input_files=1,
    )

    assert result.rows[0].user_id == "10001"
    assert result.rows[0].quality_status == ("missing_uid",)
    assert result.report.missing_uid_rows == 1
    assert result.report.coverage["numeric_uid"] == 1
