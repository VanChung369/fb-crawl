import json

import pytest

from fb_crawl.adapters.browser.profile_uid import (
    ProfileUidParser,
    ProfileUidResolver,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    SessionError,
    UidResolutionError,
)
from fb_crawl.core.models import UidResolution, UserRecord


def _html(payload: object) -> str:
    return f'<script type="application/json">{json.dumps(payload)}</script>'


def _record(user_id: str = "synthetic.user") -> UserRecord:
    return UserRecord(
        user_id=user_id,
        name="Synthetic User",
        profile_url=f"https://www.facebook.com/{user_id}",
        source="friends",
        source_url="https://www.facebook.com/example/friends",
    )


def test_parser_matches_route_vanity_to_its_numeric_user_id() -> None:
    html = _html(
        {
            "profile_id": "999999999999999",
            "initialRouteInfo": {
                "route": {
                    "rootView": {
                        "props": {
                            "viewerID": "999999999999999",
                            "userVanity": "synthetic.user",
                            "userID": "100015374200952",
                        }
                    }
                }
            },
        }
    )

    assert (
        ProfileUidParser().parse(
            html,
            expected_username="synthetic.user",
        )
        == "100015374200952"
    )


def test_parser_ignores_unpaired_viewer_and_other_user_ids() -> None:
    html = _html(
        {
            "profile_id": "999999999999999",
            "userID": "888888888888888",
            "userVanity": "another.user",
        }
    )

    assert (
        ProfileUidParser().parse(
            html,
            expected_username="synthetic.user",
        )
        is None
    )


def test_parser_rejects_conflicting_ids_for_the_same_username() -> None:
    html = _html(
        [
            {"userVanity": "synthetic.user", "userID": "100000000000001"},
            {"userVanity": "synthetic.user", "userID": "100000000000002"},
        ]
    )

    assert (
        ProfileUidParser().parse(
            html,
            expected_username="synthetic.user",
        )
        is None
    )


class Browser:
    current_url = "https://www.facebook.com/synthetic.user"

    def __init__(self, html: str) -> None:
        self.page_source = html
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)


def test_resolver_navigates_and_returns_the_matched_uid() -> None:
    browser = Browser(
        _html(
            {
                "userVanity": "synthetic.user",
                "userID": "100015374200952",
            }
        )
    )
    resolver = ProfileUidResolver(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    )

    assert resolver.resolve(browser, _record()) == UidResolution(
        "100015374200952"
    )
    assert browser.visited == ["https://www.facebook.com/synthetic.user"]


def test_resolver_reports_missing_uid_without_using_viewer_id() -> None:
    browser = Browser(_html({"profile_id": "999999999999999"}))
    resolver = ProfileUidResolver(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    )

    with pytest.raises(UidResolutionError):
        resolver.resolve(browser, _record())


def test_resolver_preserves_session_failure() -> None:
    resolver = ProfileUidResolver(
        BrowserSettings(),
        authenticated_func=lambda browser: False,
        ready_func=lambda browser, timeout: None,
    )

    with pytest.raises(SessionError):
        resolver.resolve(Browser(""), _record())
