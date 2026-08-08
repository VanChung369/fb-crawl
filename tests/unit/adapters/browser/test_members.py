import pytest

from fb_crawl.adapters.browser.members import (
    MembersCollector,
)
from fb_crawl.config import BrowserSettings

from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    SessionError,
)


class FakeBrowser:
    page_source = "<html>members</html>"

    def __init__(
        self,
        heights: list[int],
    ) -> None:
        self.heights = iter(heights)
        self.scrolls = 0
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)

    def execute_script(
        self,
        script: str,
    ):
        if script.startswith("return"):
            return next(self.heights)

        self.scrolls += 1
        return None


def test_members_collector_stops_when_height_stabilizes() -> None:
    browser = FakeBrowser(
        [
            100,
            200,
            200,
        ]
    )

    sleeps: list[float] = []

    collector = MembersCollector(
        BrowserSettings(
            browser_timeout_seconds=7,
        ),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        sleep_func=sleeps.append,
        jitter_func=lambda low, high: 0.25,
    )

    html, attempts = collector.collect(
        browser,
        ("https://www.facebook.com/" "groups/1/members"),
        steps=10,
        delay_seconds=2.0,
    )

    assert html == "<html>members</html>"
    assert attempts == 2
    assert browser.scrolls == 2
    assert sleeps == [2.25, 2.25]


def test_members_collector_never_exceeds_steps() -> None:
    browser = FakeBrowser(
        [
            100,
            200,
            300,
            400,
        ]
    )

    collector = MembersCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        sleep_func=lambda seconds: None,
        jitter_func=lambda low, high: 0.0,
    )

    _, attempts = collector.collect(
        browser,
        ("https://www.facebook.com/" "groups/1/members"),
        steps=3,
        delay_seconds=0.0,
    )

    assert attempts == 3
    assert browser.scrolls == 3


def test_members_collector_propagates_session_loss() -> None:
    collector = MembersCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: False,
        ready_func=lambda browser, timeout: None,
    )

    with pytest.raises(SessionError):
        collector.collect(
            FakeBrowser([100]),
            ("https://www.facebook.com/" "groups/1/members"),
            steps=1,
            delay_seconds=0,
        )


def test_members_collector_sanitizes_driver_failure() -> None:
    browser = FakeBrowser([100])

    def fail_navigation(
        url: str,
    ) -> None:
        raise RuntimeError("private HTML")

    browser.get = fail_navigation

    collector = MembersCollector(BrowserSettings())

    with pytest.raises(
        BrowserNavigationError,
    ) as captured:
        collector.collect(
            browser,
            ("https://www.facebook.com/" "groups/1/members"),
            steps=1,
            delay_seconds=0,
        )

    assert captured.value.target == ("https://www.facebook.com/" "groups/1/members")

    assert "private HTML" not in captured.value.safe_message
