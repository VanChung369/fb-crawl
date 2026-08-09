from fb_crawl.exporters.schema import UNIFIED_FIELDS
from fb_crawl.services.data_merge import DataMergeService, SourceRow


def row(**values: str) -> dict[str, str]:
    result = {field: "" for field in UNIFIED_FIELDS}
    result.update(values)
    return result


def test_merge_combines_aliases_fields_and_provenance_without_losing_conflicts() -> None:
    source_rows = (
        SourceRow(
            "friends.csv",
            2,
            row(
                user_id="10001",
                name="174 friends",
                profile_url="https://www.facebook.com/profile.php?id=10001",
                source="friends",
                source_url="https://www.facebook.com/a/friends",
            ),
        ),
        SourceRow(
            "repaired.csv",
            2,
            row(
                user_id="10001",
                name="Nguyen Van A",
                username="nguyenvana",
                profile_url="https://www.facebook.com/nguyenvana",
                phone_numbers="0912 345 678",
                phone_sources="facebook:post_text",
                current_city="Ha Noi",
                identity_status="repaired",
                source="profile",
                source_url="https://www.facebook.com/nguyenvana",
            ),
        ),
        SourceRow(
            "comments.csv",
            2,
            row(
                username="nguyenvana",
                name="Nguyen A",
                profile_url="https://www.facebook.com/nguyenvana",
                commented="true",
                source="comments",
                source_url="https://www.facebook.com/acme/posts/1",
            ),
        ),
    )

    result = DataMergeService().run(source_rows, input_files=3)

    assert len(result.rows) == 1
    merged = result.rows[0]
    assert merged["user_id"] == "10001"
    assert merged["name"] == "Nguyen Van A"
    assert merged["username"] == "nguyenvana"
    assert merged["phone_numbers"] == "0912 345 678"
    assert merged["phone_sources"] == "facebook:post_text"
    assert merged["commented"] == "true"
    assert merged["source"] == "friends; profile; comments"
    assert result.report.records_written == 1
    assert result.report.duplicates_merged == 2
    assert result.report.coverage["phone_numbers"] == 1
    assert any(item.field == "name" for item in result.conflicts)


def test_merge_never_uses_name_as_an_identity_key() -> None:
    result = DataMergeService().run(
        (
            SourceRow(
                "one.csv",
                2,
                row(
                    user_id="1",
                    name="Same Name",
                    profile_url="https://www.facebook.com/one",
                ),
            ),
            SourceRow(
                "two.csv",
                2,
                row(
                    user_id="2",
                    name="Same Name",
                    profile_url="https://www.facebook.com/two",
                ),
            ),
        ),
        input_files=2,
    )

    assert len(result.rows) == 2
    assert result.report.duplicates_merged == 0


def test_merge_counts_issue_rows_and_missing_quality_fields() -> None:
    result = DataMergeService().run(
        (
            SourceRow(
                "friends.csv",
                2,
                row(
                    username="only.username",
                    name="Only User",
                    profile_url="https://www.facebook.com/only.username",
                ),
            ),
            SourceRow(
                "friends.csv",
                3,
                row(
                    source="friends",
                    source_url="https://www.facebook.com/a/friends",
                    error_code="authenticated_navigation_failed",
                ),
            ),
        ),
        input_files=1,
    )

    assert result.report.rows_read == 2
    assert result.report.issue_rows == 1
    assert result.report.missing["user_id"] == 1
    assert result.report.missing["phone_numbers"] == 1
    assert result.report.repair_candidates == 1
