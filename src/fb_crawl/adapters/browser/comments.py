from __future__ import annotations

import time
from collections.abc import Callable

from selenium.common.exceptions import (
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import (
    WebDriverWait,
)

from fb_crawl.adapters.browser.crawl_budget import CrawlBudget
from fb_crawl.adapters.browser.driver import (
    wait_for_document_ready,
)
from fb_crawl.adapters.browser.session import (
    is_authenticated,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    SessionError,
)

MORE_COMMENTS_TEXTS = (
    "Xem thêm bình luận",
    "View more comments",
    "عرض مزيد من التعليقات",
    "Ver más comentarios",
    "Afficher plus de commentaires",
)

MORE_COMMENTS_XPATH = (
    "//*[self::button "
    "or self::div "
    "or self::span]["
    + " or ".join(
        ("contains(" "normalize-space(.), " f"{text!r}" ")")
        for text in MORE_COMMENTS_TEXTS
    )
    + "]"
)


def _first_clickable(browser):
    elements = browser.find_elements(
        By.XPATH,
        MORE_COMMENTS_XPATH,
    )

    for element in elements:
        if element.is_displayed() and element.is_enabled():
            return element

    return False


class CommentsCollector:
    def __init__(
        self,
        settings: BrowserSettings,
        *,
        authenticated_func: Callable[
            [object],
            bool,
        ] = is_authenticated,
        ready_func: Callable[
            [object, float],
            None,
        ] = wait_for_document_ready,
        wait_factory=WebDriverWait,
        sleep_func: Callable[
            [float],
            None,
        ] = time.sleep,
        monotonic_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._authenticated = authenticated_func
        self._ready = ready_func
        self._wait_factory = wait_factory
        self._sleep = sleep_func
        self._monotonic = monotonic_func

    def collect(
        self,
        browser,
        url: str,
        *,
        steps: int | None,
        delay_seconds: float,
        max_duration_seconds: float | None = None,
    ) -> tuple[str, int]:
        try:
            browser.get(url)

            self._ready(
                browser,
                self._settings.browser_timeout_seconds,
            )

            if not self._authenticated(browser):
                raise SessionError(
                    "The authenticated Facebook " "session is no longer valid."
                )

            attempts = 0
            budget = CrawlBudget(
                steps=steps,
                max_duration_seconds=max_duration_seconds,
                monotonic_func=self._monotonic,
            )

            while budget.allows(attempts):
                browser.execute_script(
                    "window.scrollTo(" "0, document.body.scrollHeight" ")"
                )

                attempts += 1

                try:
                    candidate = self._wait_factory(
                        browser,
                        budget.wait_timeout(
                            self._settings.browser_timeout_seconds
                        ),
                    ).until(_first_clickable)

                except TimeoutException:
                    break

                if not candidate:
                    break

                candidate.click()

                if delay_seconds:
                    self._sleep(delay_seconds)

            return (
                str(browser.page_source),
                attempts,
            )

        except SessionError:
            raise

        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated comments " "navigation failed.",
                target=url,
            ) from error
