import pytest

from fb_crawl.adapters.browser.members import (
    MembersCollector,
)
from fb_crawl.config import BrowserSettings

from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    RateLimitError,
    SessionError,
)
from fb_crawl.services.execution_control import CrawlCancelled


class RecordingControl:
    def __init__(self, *, cancelled: bool = False, cancel_after_progress: bool = False):
        self.cancelled = cancelled
        self.cancel_after_progress = cancel_after_progress
        self.events = []

    def is_cancel_requested(self) -> bool:
        return self.cancelled

    def check_account_safety(self, browser):
        return None

    def emit(self, event_type, *, counters=None, safe_message=""):
        self.events.append((event_type, dict(counters or {})))
        if self.cancel_after_progress:
            self.cancelled = True


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


def test_members_collector_without_limits_stops_at_exhaustion_signal() -> None:
    browser = FakeBrowser([100, 200, 200])
    collector = MembersCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        sleep_func=lambda seconds: None,
        jitter_func=lambda low, high: 0.0,
    )

    _, attempts = collector.collect(
        browser,
        "https://www.facebook.com/groups/1/members",
        steps=None,
        delay_seconds=0,
        max_duration_seconds=None,
    )

    assert attempts == 2


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


def test_members_collector_preserves_rate_limit_signal() -> None:
    collector = MembersCollector(
        BrowserSettings(),
        ready_func=lambda browser, timeout: (_ for _ in ()).throw(
            RateLimitError("Facebook temporarily limited requests.")
        ),
    )

    with pytest.raises(RateLimitError):
        collector.collect(
            FakeBrowser([100]),
            "https://www.facebook.com/groups/1/members",
            steps=1,
            delay_seconds=0,
        )


def test_members_cancellation_prevents_navigation_and_second_scroll() -> None:
    control = RecordingControl(cancelled=True)
    browser = FakeBrowser([100, 200, 300])
    collector = MembersCollector(BrowserSettings(), control=control)

    with pytest.raises(CrawlCancelled):
        collector.collect(browser, "https://www.facebook.com/groups/1/members", steps=2, delay_seconds=0)
    assert browser.visited == []

    control = RecordingControl(cancel_after_progress=True)
    collector = MembersCollector(
        BrowserSettings(), control=control, authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None, sleep_func=lambda seconds: None,
        jitter_func=lambda low, high: 0,
    )
    with pytest.raises(CrawlCancelled):
        collector.collect(browser, "https://www.facebook.com/groups/1/members", steps=2, delay_seconds=0)
    assert browser.scrolls == 1
    assert control.events == [("target_progress", {"steps_completed": 1})]
