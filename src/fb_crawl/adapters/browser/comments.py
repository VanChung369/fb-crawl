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
    RateLimitError,
    SessionError,
)
from fb_crawl.services.execution_control import AccountSafetyStop, CrawlCancelled, JobBudgetReached, ExecutionControl, NavigationPacer, NOOP_EXECUTION_CONTROL, NOOP_NAVIGATION_PACER, guard_cancellation, guard_execution

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
        control: ExecutionControl = NOOP_EXECUTION_CONTROL,
        navigation_pacer: NavigationPacer = NOOP_NAVIGATION_PACER,
    ) -> None:
        self._settings = settings
        self._authenticated = authenticated_func
        self._ready = ready_func
        self._wait_factory = wait_factory
        self._sleep = sleep_func
        self._monotonic = monotonic_func
        self._control = control
        self._navigation_pacer = navigation_pacer

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
            guard_cancellation(self._control)
            self._navigation_pacer.wait()
            browser.get(url)

            self._ready(
                browser,
                self._settings.browser_timeout_seconds,
            )

            guard_execution(self._control, browser)
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
                guard_execution(self._control, browser)
                browser.execute_script(
                    "window.scrollTo(" "0, document.body.scrollHeight" ")"
                )

                attempts += 1
                self._control.emit("target_progress", counters={"steps_completed": attempts})

                try:
                    guard_execution(self._control, browser)
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

                guard_execution(self._control, browser)
                candidate.click()

                if delay_seconds:
                    self._sleep(delay_seconds)

            guard_execution(self._control, browser)
            return (
                str(browser.page_source),
                attempts,
            )

        except (SessionError, RateLimitError, CrawlCancelled, JobBudgetReached, AccountSafetyStop):
            raise

        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated comments " "navigation failed.",
                target=url,
            ) from error
