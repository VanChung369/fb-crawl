import pytest

from fb_crawl.core.urls import (
    classify_authenticated_url,
    normalize_comments_url,
    normalize_members_url,
    profile_directory_urls,
)

from fb_crawl.core.models import AuthenticatedAction


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


def test_batch_classifier_returns_action_and_normalized_url() -> None:
    assert classify_authenticated_url("https://facebook.com/groups/1") == (
        AuthenticatedAction.MEMBERS,
        "https://www.facebook.com/groups/1/members",
    )

    assert classify_authenticated_url("https://facebook.com/acme/posts/2") == (
        AuthenticatedAction.COMMENTS,
        "https://www.facebook.com/acme/posts/2",
    )


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.facebook.com/places/Ha-Noi/123",
        "https://www.facebook.com/login",
        "https://www.facebook.com/checkpoint/",
        "https://www.facebook.com/example",
        "https://example.test/groups/1",
    ],
)
def test_batch_classifier_rejects_unsupported_urls(
    raw: str,
) -> None:
    assert classify_authenticated_url(raw) is None


@pytest.mark.parametrize(
    ("profile_url", "user_id", "expected"),
    [
        (
            "https://m.facebook.com/profile.php?id=123&ref=share#top",
            "123",
            (
                (
                    "https://www.facebook.com/profile.php"
                    "?id=123&sk=directory_personal_details"
                ),
                (
                    "https://www.facebook.com/profile.php"
                    "?id=123&sk=directory_links"
                ),
            ),
        ),
        (
            "https://web.facebook.com/synthetic.user?ref=share#top",
            "synthetic.user",
            (
                (
                    "https://www.facebook.com/synthetic.user"
                    "/directory_personal_details"
                ),
                (
                    "https://www.facebook.com/synthetic.user"
                    "/directory_links"
                ),
            ),
        ),
    ],
)
def test_profile_directory_urls_are_normalized_and_bounded(
    profile_url: str,
    user_id: str,
    expected: tuple[str, ...],
) -> None:
    assert profile_directory_urls(profile_url, user_id) == expected


@pytest.mark.parametrize(
    ("profile_url", "user_id"),
    [
        ("https://example.test/synthetic.user", "synthetic.user"),
        ("https://www.facebook.com/login", "login"),
        ("https://www.facebook.com/checkpoint/", "checkpoint"),
        (
            "https://www.facebook.com/two_step_verification/",
            "two_step_verification",
        ),
        ("https://www.facebook.com/groups/1", "1"),
        ("https://www.facebook.com/synthetic.user/posts/1", "synthetic.user"),
        ("https://www.facebook.com/profile.php?id=123", "456"),
        ("https://www.facebook.com/synthetic.user", "different.user"),
        ("https://www.facebook.com/synthetic.user/about", "synthetic.user"),
        (None, "synthetic.user"),
        ("https://www.facebook.com/synthetic.user", ""),
    ],
)
def test_profile_directory_urls_reject_unsupported_or_mismatched_identity(
    profile_url: str | None,
    user_id: str,
) -> None:
    assert profile_directory_urls(profile_url, user_id) == ()
