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


def test_parser_accepts_handles_and_filters_action_labels() -> None:
    records = UserParser().parse(
        (FIXTURES / "comments.html").read_text(encoding="utf-8"),
        source="comments",
        source_url=("https://www.facebook.com/example/posts/1"),
    )

    assert [(record.user_id, record.name) for record in records] == [
        ("synthetic.handle", "Handle Name"),
        ("202", "User 202"),
    ]

    assert records[0].username == "synthetic.handle"
    assert records[1].username is None

    assert all("comment_id" not in record.profile_url for record in records)


def test_parser_keeps_valid_identity_when_name_is_unavailable() -> None:
    records = UserParser().parse(
        '<a href="/user/203/"></a>',
        source="comments",
        source_url=("https://www.facebook.com/example/posts/1"),
    )

    assert [(record.user_id, record.name) for record in records] == [
        ("203", None),
    ]


def test_relationship_parser_accepts_plain_visible_profile_links() -> None:
    records = UserParser(allow_plain_profile_links=True).parse(
        """
        <main>
          <a href="/synthetic.friend">Synthetic Friend</a>
          <a href="/messages">Messages</a>
        </main>
        """,
        source="friends",
        source_url="https://www.facebook.com/synthetic.user/friends",
    )

    assert [(record.user_id, record.name) for record in records] == [
        ("synthetic.friend", "Synthetic Friend")
    ]
    assert records[0].username == "synthetic.friend"


def test_parser_replaces_friend_count_label_with_later_profile_name() -> None:
    records = UserParser(allow_plain_profile_links=True).parse(
        """
        <main>
          <a href="/profile.php?id=61573323749006">174 friends</a>
          <a href="/profile.php?id=61573323749006">Hiếu Văn</a>
        </main>
        """,
        source="friends",
        source_url="https://www.facebook.com/synthetic.user/friends",
    )

    assert len(records) == 1
    assert records[0].user_id == "61573323749006"
    assert records[0].name == "Hiếu Văn"


def test_parser_uses_image_alt_when_visible_text_is_social_context() -> None:
    records = UserParser(allow_plain_profile_links=True).parse(
        """
        <a href="/profile.php?id=61573323749006">
          174 friends
          <img alt="Hiếu Văn">
        </a>
        """,
        source="friends",
        source_url="https://www.facebook.com/synthetic.user/friends",
    )

    assert len(records) == 1
    assert records[0].name == "Hiếu Văn"
