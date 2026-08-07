import pytest

from fb_crawl.core.urls import normalize_members_url


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
