from pathlib import Path

from fb_crawl.adapters.browser.user_parser import UserParser

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "authenticated"


def test_parser_extracts_member_identity_forms_and_deduplicates() -> None:
    records = UserParser().parse(
        (FIXTURES / "members.html").read_text(encoding="utf-8"),
        source="members",
        source_url=("https://www.facebook.com/groups/100/members"),
    )

    assert [record.user_id for record in records] == ["200", "201"]

    assert records[0].profile_url == ("https://www.facebook.com/profile.php?id=200")

    assert records[1].name == "Member Two"
