from fb_crawl.adapters.browser.reaction_parser import ReactionParser


def test_reaction_parser_extracts_only_explicit_accessible_reaction_type() -> None:
    records = ReactionParser().parse(
        """
        <div role="dialog">
          <div aria-label="Love reaction">
            <a href="/synthetic.one">Synthetic One</a>
          </div>
          <div>
            <a href="/synthetic.two">Synthetic Two</a>
          </div>
        </div>
        """,
        source="reactions",
        source_url="https://www.facebook.com/acme/posts/1",
    )

    assert [(item.user_id, item.reaction_types) for item in records] == [
        ("synthetic.one", ("love",)),
        ("synthetic.two", ()),
    ]
    assert all(item.reacted for item in records)
    assert all(item.interaction_count == 1 for item in records)


def test_reaction_parser_normalizes_vietnamese_labels() -> None:
    records = ReactionParser().parse(
        """
        <div aria-label="Phẫn nộ">
          <a href="/synthetic.user">Synthetic User</a>
        </div>
        """,
        source="reactions",
        source_url="https://www.facebook.com/acme/posts/1",
    )

    assert records[0].reaction_types == ("angry",)
