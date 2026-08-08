import pytest

import json
from pathlib import Path

from fb_crawl.adapters.browser.session import (
    SessionStore,
    is_authenticated,
)


class FakeBrowser:
    def __init__(
        self,
        cookies=None,
        current_url: str = ("https://www.facebook.com/"),
    ) -> None:
        self.cookies = list(cookies or [])
        self.current_url = current_url
        self.added: list[dict[str, object]] = []
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.current_url = url
        self.visited.append(url)

    def get_cookies(self):
        return list(self.cookies or self.added)

    def add_cookie(self, cookie) -> None:
        self.added.append(cookie)

    def refresh(self) -> None:
        self.cookies = list(self.added)


def test_authentication_requires_c_user_cookie() -> None:
    authenticated = FakeBrowser(
        [
            {
                "name": "c_user",
                "value": "100",
            }
        ]
    )

    anonymous = FakeBrowser([])

    assert is_authenticated(authenticated) is True
    assert is_authenticated(anonymous) is False


@pytest.mark.parametrize(
    "current_url",
    [
        "https://www.facebook.com/login",
        "https://www.facebook.com/checkpoint/123",
        ("https://www.facebook.com/" "two_step_verification/"),
    ],
)
def test_authentication_rejects_verification_routes(
    current_url: str,
) -> None:
    browser = FakeBrowser(
        [
            {
                "name": "c_user",
                "value": "100",
            }
        ],
        current_url=current_url,
    )

    assert is_authenticated(browser) is False


def test_restore_filters_cookie_fields_and_revalidates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.json"

    path.write_text(
        json.dumps(
            [
                {
                    "name": "c_user",
                    "value": "100",
                    "domain": ".facebook.com",
                    "sameSite": "Lax",
                    "unsupported": "secret",
                },
                {
                    "name": 7,
                    "value": "invalid",
                },
            ]
        ),
        encoding="utf-8",
    )

    browser = FakeBrowser()

    assert SessionStore(path).restore(browser) is True

    assert browser.visited == ["https://www.facebook.com/"]

    assert browser.added == [
        {
            "name": "c_user",
            "value": "100",
            "domain": ".facebook.com",
            "sameSite": "Lax",
        }
    ]


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        "{}",
        "[1, null]",
    ],
)
def test_restore_treats_malformed_content_as_unavailable(
    content: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        content,
        encoding="utf-8",
    )

    assert SessionStore(path).restore(FakeBrowser()) is False
