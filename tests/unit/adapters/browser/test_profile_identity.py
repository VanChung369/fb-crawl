import json

import pytest

from fb_crawl.adapters.browser.profile_identity import (
    ProfileIdentityParser,
    ProfileIdentityResolver,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    IdentityResolutionError,
    RateLimitError,
    SessionError,
)
from fb_crawl.core.models import ProfileIdentity, UserRecord


def _html(payload: object) -> str:
    return f'<script type="application/json">{json.dumps(payload)}</script>'


def _record(
    user_id: str = "61573323749006",
    *,
    username: str | None = None,
) -> UserRecord:
    profile_url = (
        f"https://www.facebook.com/{username}"
        if username
        else f"https://www.facebook.com/profile.php?id={user_id}"
    )
    return UserRecord(
        user_id=user_id,
        name="174 friends",
        username=username,
        profile_url=profile_url,
        source="friends",
        source_url="https://www.facebook.com/synthetic.user/friends",
    )


def test_parser_extracts_name_and_accepts_absent_vanity_for_numeric_profile() -> None:
    identity = ProfileIdentityParser().parse(
        _html(
            {
                "id": "61573323749006",
                "name": "Hiếu Văn",
                "username_for_profile": None,
            }
        ),
        expected_user_id="61573323749006",
        current_url="https://www.facebook.com/profile.php?id=61573323749006",
    )

    assert identity == ProfileIdentity(
        user_id="61573323749006",
        name="Hiếu Văn",
        username=None,
        profile_url=(
            "https://www.facebook.com/profile.php?id=61573323749006"
        ),
    )


def test_parser_resolves_vanity_profile_to_uid_and_canonical_url() -> None:
    identity = ProfileIdentityParser().parse(
        _html(
            {
                "userVanity": "synthetic.user",
                "userID": "100015374200952",
                "name": "Synthetic User",
            }
        ),
        expected_username="synthetic.user",
        current_url="https://www.facebook.com/synthetic.user",
    )

    assert identity == ProfileIdentity(
        user_id="100015374200952",
        name="Synthetic User",
        username="synthetic.user",
        profile_url="https://www.facebook.com/synthetic.user",
    )


def test_parser_ignores_unrelated_viewer_identity() -> None:
    assert (
        ProfileIdentityParser().parse(
            _html(
                {
                    "id": "999999999999999",
                    "name": "Unrelated Viewer",
                }
            ),
            expected_user_id="61573323749006",
        )
        is None
    )


class Browser:
    def __init__(self, html: str) -> None:
        self.page_source = html
        self.current_url = "https://www.facebook.com/profile.php?id=61573323749006"
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)


def test_resolver_navigates_once_and_returns_profile_identity() -> None:
    browser = Browser(
        _html({"id": "61573323749006", "name": "Hiếu Văn"})
    )
    resolver = ProfileIdentityResolver(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    )

    assert resolver.resolve(browser, _record()).name == "Hiếu Văn"
    assert browser.visited == [_record().profile_url]


def test_resolver_reports_missing_matching_identity() -> None:
    resolver = ProfileIdentityResolver(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    )

    with pytest.raises(IdentityResolutionError):
        resolver.resolve(Browser(_html({"id": "999999999999999"})), _record())


def test_resolver_preserves_session_failure() -> None:
    resolver = ProfileIdentityResolver(
        BrowserSettings(),
        authenticated_func=lambda browser: False,
        ready_func=lambda browser, timeout: None,
    )

    with pytest.raises(SessionError):
        resolver.resolve(Browser(""), _record())


def test_resolver_detects_visible_facebook_rate_limit_surface() -> None:
    resolver = ProfileIdentityResolver(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    )
    browser = Browser(
        "<html><body>We limit how often you can do certain things.</body></html>"
    )

    with pytest.raises(RateLimitError):
        resolver.resolve(browser, _record())
