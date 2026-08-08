import pytest

from fb_crawl.adapters.browser.comments import (
    CommentsCollector,
    MORE_COMMENTS_TEXTS,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    SessionError,
)


class Candidate:
    def __init__(self) -> None:
        self.clicks = 0

    def is_displayed(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def click(self) -> None:
        self.clicks += 1


class Browser:
    page_source = "<html>comments</html>"

    def __init__(
        self,
        candidates: list[list[Candidate]],
    ) -> None:
        self.current_url = "https://www.facebook.com/" "example/posts/1"

        self.cookies = [
            {
                "name": "c_user",
                "value": "100",
            }
        ]

        self.candidates = iter(candidates)
        self.scrolls = 0

    def get(self, url: str) -> None:
        self.current_url = url

    def get_cookies(self):
        return list(self.cookies)

    def execute_script(
        self,
        script: str,
    ) -> None:
        self.scrolls += 1

    def find_elements(
        self,
        by: str,
        value: str,
    ):
        return next(self.candidates)


def test_phrase_list_is_valid_multilingual_unicode() -> None:
    assert "Xem thêm bình luận" in MORE_COMMENTS_TEXTS

    assert "View more comments" in MORE_COMMENTS_TEXTS

    assert "عرض مزيد من التعليقات" in MORE_COMMENTS_TEXTS

    assert "Ver más comentarios" in MORE_COMMENTS_TEXTS

    assert "Afficher plus de commentaires" in MORE_COMMENTS_TEXTS


def test_comments_collector_uses_one_wait_per_attempt_and_stops_early() -> None:
    first = Candidate()
    browser = Browser(
        [
            [first],
            [],
        ]
    )

    waits: list[float] = []

    class Wait:
        def __init__(
            self,
            browser,
            timeout: float,
        ) -> None:
            waits.append(timeout)
            self.browser = browser

        def until(self, predicate):
            return predicate(self.browser)

    collector = CommentsCollector(
        BrowserSettings(
            browser_timeout_seconds=6,
        ),
        ready_func=lambda browser, timeout: None,
        wait_factory=Wait,
        sleep_func=lambda seconds: None,
    )

    html, attempts = collector.collect(
        browser,
        browser.current_url,
        steps=10,
        delay_seconds=0,
    )

    assert html == "<html>comments</html>"
    assert attempts == 2
    assert waits == [6, 6]
    assert first.clicks == 1


def test_comments_collector_never_exceeds_steps() -> None:
    candidates = [Candidate() for _ in range(3)]

    browser = Browser([[candidate] for candidate in candidates])

    class Wait:
        def __init__(
            self,
            browser,
            timeout: float,
        ) -> None:
            self.browser = browser

        def until(self, predicate):
            return predicate(self.browser)

    _, attempts = CommentsCollector(
        BrowserSettings(),
        ready_func=lambda browser, timeout: None,
        wait_factory=Wait,
        sleep_func=lambda seconds: None,
    ).collect(
        browser,
        browser.current_url,
        steps=3,
        delay_seconds=0,
    )

    assert attempts == 3

    assert sum(candidate.clicks for candidate in candidates) == 3


def test_comments_collector_propagates_session_loss() -> None:
    browser = Browser([])

    collector = CommentsCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: False,
        ready_func=lambda browser, timeout: None,
    )

    with pytest.raises(SessionError):
        collector.collect(
            browser,
            browser.current_url,
            steps=1,
            delay_seconds=0,
        )


def test_comments_collector_sanitizes_click_failure() -> None:
    candidate = Candidate()

    candidate.click = lambda: ((_ for _ in ()).throw(RuntimeError("private DOM")))

    browser = Browser(
        [
            [candidate],
        ]
    )

    class Wait:
        def __init__(
            self,
            browser,
            timeout: float,
        ) -> None:
            self.browser = browser

        def until(self, predicate):
            return predicate(self.browser)

    collector = CommentsCollector(
        BrowserSettings(),
        ready_func=lambda browser, timeout: None,
        wait_factory=Wait,
    )

    with pytest.raises(
        BrowserNavigationError,
    ) as captured:
        collector.collect(
            browser,
            browser.current_url,
            steps=1,
            delay_seconds=0,
        )

    assert captured.value.target == browser.current_url

    assert "private DOM" not in captured.value.safe_message
