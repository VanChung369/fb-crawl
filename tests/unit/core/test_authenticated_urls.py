import pytest

from fb_crawl.core.urls import (
    normalize_comments_url,
    normalize_members_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://facebook.com/groups/pythonvn",
            "https://www.facebook.com/groups/pythonvn/members",
        ),
        (
            "https://m.facebook.com/groups/123/members?ref=share#top",
            "https://www.facebook.com/groups/123/members",
        ),
    ],
)
def test_normalize_members_url(
    raw: str,
    expected: str,
) -> None:
    assert normalize_members_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.facebook.com/places/Ha-Noi/123",
        "https://www.facebook.com/login",
        "https://www.facebook.com/example",
        "https://www.facebook.com/groups/1/posts/2",
        "https://example.test/groups/1/members",
    ],
)
def test_members_url_rejects_unsupported_targets(
    raw: str,
) -> None:
    assert normalize_members_url(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://facebook.com/groups/10/posts/20?ref=share",
            "https://www.facebook.com/groups/10/posts/20",
        ),
        (
            "https://www.facebook.com/example/posts/20/",
            "https://www.facebook.com/example/posts/20",
        ),
        (
            "https://www.facebook.com/example/videos/30",
            "https://www.facebook.com/example/videos/30",
        ),
        (
            "https://www.facebook.com/reel/40",
            "https://www.facebook.com/reel/40",
        ),
        (
            ("https://www.facebook.com/permalink.php" "?id=9&story_fbid=50&ref=x"),
            ("https://www.facebook.com/permalink.php" "?story_fbid=50&id=9"),
        ),
        (
            ("https://www.facebook.com/photo.php" "?fbid=60&id=9&set=x"),
            ("https://www.facebook.com/photo.php" "?fbid=60&id=9"),
        ),
    ],
)
def test_normalize_comments_url(
    raw: str,
    expected: str,
) -> None:
    assert normalize_comments_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.facebook.com/places/Ha-Noi/123",
        "https://www.facebook.com/login",
        "https://www.facebook.com/checkpoint/",
        "https://www.facebook.com/example",
        "https://www.facebook.com/groups/1/members",
        "https://example.test/example/posts/1",
    ],
)
def test_comments_url_rejects_unsupported_targets(
    raw: str,
) -> None:
    assert normalize_comments_url(raw) is None
