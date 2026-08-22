import pytest

from fb_crawl.adapters.browser.relationships import RelationshipCollector
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError
from fb_crawl.services.execution_control import CrawlCancelled


class Browser:
    page_source = "<html>fallback</html>"

    def __init__(self, heights: list[int]) -> None:
        self.heights = iter(heights)
        self.scrolls = 0
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)

    def execute_script(self, script: str):
        if "outerHTML" in script:
            return "<main><a href='/friend.one'>Friend One</a></main>"
        if script.startswith("return"):
            return next(self.heights)
        self.scrolls += 1
        return None


def test_relationship_collector_scrolls_bounded_main_content() -> None:
    browser = Browser([100, 200, 200])
    collector = RelationshipCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        sleep_func=lambda seconds: None,
        jitter_func=lambda low, high: 0,
    )

    html, attempts = collector.collect(
        browser,
        "https://www.facebook.com/synthetic.user/friends",
        steps=5,
        delay_seconds=0,
    )

    assert html.startswith("<main>")
    assert attempts == 2
    assert browser.scrolls == 2


def test_relationship_collector_propagates_session_loss() -> None:
    collector = RelationshipCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: False,
        ready_func=lambda browser, timeout: None,
    )

    with pytest.raises(SessionError):
        collector.collect(
            Browser([100]),
            "https://www.facebook.com/synthetic.user/followers",
            steps=1,
            delay_seconds=0,
        )


def test_relationship_collector_sanitizes_navigation_failure() -> None:
    browser = Browser([100])
    browser.get = lambda url: (_ for _ in ()).throw(RuntimeError("private DOM"))

    with pytest.raises(BrowserNavigationError) as captured:
        RelationshipCollector(BrowserSettings()).collect(
            browser,
            "https://www.facebook.com/synthetic.user/friends",
            steps=1,
            delay_seconds=0,
        )

    assert "private DOM" not in captured.value.safe_message


def test_relationship_cancelled_before_navigation_does_not_visit_target() -> None:
    class Cancelled:
        def is_cancel_requested(self): return True
        def emit(self, event_type, *, counters=None, safe_message=""): return None
        def check_account_safety(self, browser): return None
    browser = Browser([100])
    with pytest.raises(CrawlCancelled):
        RelationshipCollector(BrowserSettings(), control=Cancelled()).collect(browser, "https://www.facebook.com/synthetic.user/friends", steps=1, delay_seconds=0)
    assert browser.visited == []


def test_relationship_cancel_after_progress_has_no_second_scroll() -> None:
    class Control:
        cancelled = False
        events = []
        def is_cancel_requested(self): return self.cancelled
        def emit(self, event_type, *, counters=None, safe_message=""):
            self.events.append((event_type, dict(counters or {}))); self.cancelled = True
        def check_account_safety(self, browser): return None
    control = Control()
    browser = Browser([100, 200])
    with pytest.raises(CrawlCancelled):
        RelationshipCollector(BrowserSettings(), control=control, authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None, sleep_func=lambda seconds: None,
            jitter_func=lambda low, high: 0).collect(browser, "https://www.facebook.com/synthetic.user/friends", steps=2, delay_seconds=0)
    assert browser.scrolls == 1
    assert control.events == [("target_progress", {"steps_completed": 1})]
