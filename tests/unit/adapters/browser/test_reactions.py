import pytest
from selenium.webdriver.common.by import By

from fb_crawl.adapters.browser.reactions import ReactionsCollector
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError
from fb_crawl.services.execution_control import CrawlCancelled


class Element:
    def __init__(self) -> None:
        self.clicks = 0

    def is_displayed(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def click(self) -> None:
        self.clicks += 1


class Browser:
    page_source = "<html>post</html>"

    def __init__(self) -> None:
        self.button = Element()
        self.dialog = Element()
        self.heights = iter([100, 200, 200])
        self.get_calls = 0
        self.scripts: list[str] = []

    def get(self, url: str) -> None:
        self.get_calls += 1
        self.current_url = url

    def find_elements(self, by: str, value: str):
        return [self.button] if by == By.XPATH else [self.dialog]

    def execute_script(self, script: str):
        self.scripts.append(script)
        if "outerHTML" in script:
            return "<div role='dialog'><a href='/friend.one'>Friend One</a></div>"
        return next(self.heights)


class Wait:
    def __init__(self, browser, timeout: float) -> None:
        self.browser = browser

    def until(self, predicate):
        result = predicate(self.browser)
        if not result:
            raise RuntimeError("timeout")
        return result


def test_reactions_collector_opens_dialog_and_scrolls_bounded() -> None:
    browser = Browser()
    collector = ReactionsCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        wait_factory=Wait,
        sleep_func=lambda seconds: None,
    )

    html, attempts = collector.collect(
        browser,
        "https://www.facebook.com/acme/posts/1",
        steps=5,
        delay_seconds=0,
    )

    assert "role='dialog'" in html
    assert attempts == 3
    assert browser.button.clicks == 1


def test_reactions_collector_propagates_session_loss() -> None:
    with pytest.raises(SessionError):
        ReactionsCollector(
            BrowserSettings(),
            authenticated_func=lambda browser: False,
            ready_func=lambda browser, timeout: None,
        ).collect(
            Browser(),
            "https://www.facebook.com/acme/posts/1",
            steps=1,
            delay_seconds=0,
        )


def test_reactions_collector_sanitizes_dom_failure() -> None:
    browser = Browser()
    browser.find_elements = lambda by, value: (_ for _ in ()).throw(
        RuntimeError("private reactions DOM")
    )

    with pytest.raises(BrowserNavigationError) as captured:
        ReactionsCollector(
            BrowserSettings(),
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
            wait_factory=Wait,
        ).collect(
            browser,
            "https://www.facebook.com/acme/posts/1",
            steps=1,
            delay_seconds=0,
        )

    assert "private reactions DOM" not in captured.value.safe_message


def test_reactions_cancelled_before_navigation_does_not_open_dialog() -> None:
    class Cancelled:
        def is_cancel_requested(self): return True
        def emit(self, event_type, *, counters=None, safe_message=""): return None
        def check_account_safety(self, browser): return None
    browser = Browser()
    with pytest.raises(CrawlCancelled):
        ReactionsCollector(BrowserSettings(), control=Cancelled()).collect(browser, "https://www.facebook.com/acme/posts/1", steps=1, delay_seconds=0)
    assert browser.get_calls == 0
    assert browser.button.clicks == 0


def test_reactions_cancel_after_progress_has_no_second_scroll() -> None:
    class Control:
        cancelled = False
        events = []
        def is_cancel_requested(self): return self.cancelled
        def emit(self, event_type, *, counters=None, safe_message=""):
            self.events.append((event_type, dict(counters or {}))); self.cancelled = True
        def check_account_safety(self, browser): return None
    control = Control()
    browser = Browser()
    with pytest.raises(CrawlCancelled):
        ReactionsCollector(BrowserSettings(), control=control, authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None, wait_factory=Wait).collect(browser, "https://www.facebook.com/acme/posts/1", steps=2, delay_seconds=0)
    assert sum("scrollTop" in script for script in browser.scripts) == 1
    assert control.events == [("target_progress", {"steps_completed": 1})]
