import pytest

from fb_crawl.adapters.browser.messages import MessagesCollector
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError


class Browser:
    page_source = "<html>fallback</html>"

    def __init__(self, heights: list[int]) -> None:
        self.heights = iter(heights)

    def get(self, url: str) -> None:
        self.current_url = url

    def execute_script(self, script: str):
        if "outerHTML" in script:
            return "<main data-message-id='1'>Hello</main>"
        return next(self.heights)


def test_messages_collector_scrolls_up_with_a_bound() -> None:
    browser = Browser([100, 200, 200])
    html, attempts = MessagesCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        sleep_func=lambda seconds: None,
    ).collect(
        browser,
        "https://www.facebook.com/messages/t/123",
        steps=5,
        delay_seconds=0,
    )

    assert "data-message-id" in html
    assert attempts == 3


def test_messages_collector_propagates_session_loss() -> None:
    with pytest.raises(SessionError):
        MessagesCollector(
            BrowserSettings(),
            authenticated_func=lambda browser: False,
            ready_func=lambda browser, timeout: None,
        ).collect(
            Browser([100]),
            "https://www.facebook.com/messages/t/123",
            steps=1,
            delay_seconds=0,
        )


def test_messages_collector_sanitizes_navigation_failure() -> None:
    browser = Browser([100])
    browser.get = lambda url: (_ for _ in ()).throw(RuntimeError("private text"))

    with pytest.raises(BrowserNavigationError) as captured:
        MessagesCollector(BrowserSettings()).collect(
            browser,
            "https://www.facebook.com/messages/t/123",
            steps=1,
            delay_seconds=0,
        )

    assert "private text" not in captured.value.safe_message
