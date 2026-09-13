from __future__ import annotations

import time
from collections.abc import Callable

from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import (
    WebDriverWait,
)

from fb_crawl.adapters.browser.crawl_budget import CrawlBudget, CrawlCollection
from fb_crawl.adapters.browser.driver import (
    wait_for_document_ready,
)
from fb_crawl.adapters.browser.human import human_scroll
from fb_crawl.adapters.browser.session import (
    is_authenticated,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    RateLimitError,
    SessionError,
)
from fb_crawl.services.execution_control import AccountSafetyStop, CrawlCancelled, JobBudgetReached, ExecutionControl, NavigationPacer, NOOP_EXECUTION_CONTROL, NOOP_NAVIGATION_PACER, cooperative_wait, guard_cancellation, guard_execution

MORE_COMMENTS_TEXTS = (
    "Xem thêm bình luận",
    "View more comments",
    "عرض مزيد من التعليقات",
    "Ver más comentarios",
    "Afficher plus de commentaires",
    "View previous comments",
    "Xem các bình luận trước",
    "Xem bình luận trước",
    "View more replies",
    "View previous replies",
    "Xem thêm câu trả lời",
    "Xem các câu trả lời trước",
)

MORE_COMMENTS_XPATH = (
    "//*[self::button or @role='button' or @role='link' or self::span[not(*)]]["
    + " or ".join(
        ("contains(" "normalize-space(.), " f"{text!r}" ")")
        for text in MORE_COMMENTS_TEXTS
    )
    + "]"
)

REPLIES_XPATH = (
    "//*[self::button or @role='button' or @role='link' or self::span[not(*)]]"
    "[(contains(normalize-space(.), ' replies') or contains(normalize-space(.), ' reply') "
    "or contains(normalize-space(.), ' phản hồi') or contains(normalize-space(.), ' câu trả lời')) "
    "and string-length(normalize-space(.)) < 100 "
    "and (starts-with(normalize-space(.), 'View ') or starts-with(normalize-space(.), 'Xem ') "
    "or contains('0123456789', substring(normalize-space(.), 1, 1)))]"
)
SORT_XPATH = (
    "//*[self::button or @role='button'][normalize-space(.)='Most relevant' "
    "or normalize-space(.)='Phù hợp nhất' or normalize-space(.)='Relevant' "
    "or normalize-space(.)='Newest' or normalize-space(.)='Mới nhất']"
)
ALL_COMMENTS_XPATH = (
    "//*[@role='menuitem' or @role='menuitemradio' or @role='option']"
    "[normalize-space(.)='All comments' or normalize-space(.)='Tất cả bình luận' "
    "or .//*[normalize-space(.)='All comments' or normalize-space(.)='Tất cả bình luận']]"
)


def _first_clickable(browser, *, sort_requested=False, sort_selected=False):
    elements = browser.find_elements(
        By.XPATH,
        ' | '.join([ALL_COMMENTS_XPATH] + ([] if sort_requested or sort_selected else [SORT_XPATH]) + [MORE_COMMENTS_XPATH, REPLIES_XPATH]),
    )

    def priority(element):
        try:
            label = getattr(element, 'text', '').strip()
            if label.startswith(('All comments', 'Tất cả bình luận')):
                return 0
            if label in ('Most relevant', 'Phù hợp nhất', 'Relevant', 'Newest', 'Mới nhất'):
                return 1
        except StaleElementReferenceException:
            pass
        return 2

    for element in sorted(elements, key=priority):
        try:
            if element.is_displayed() and element.is_enabled():
                return element
        except StaleElementReferenceException:
            continue

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
    ) -> CrawlCollection:
        try:
            budget = CrawlBudget(
                steps=steps,
                max_duration_seconds=max_duration_seconds,
                monotonic_func=self._monotonic,
            )
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
            natural_complete = False
            wait_exhausted = False
            sort_requested = False
            sort_selected = False

            while budget.allows(attempts):
                guard_execution(self._control, browser)
                human_scroll(browser)

                attempts += 1
                self._control.emit("target_progress", counters={"steps_completed": attempts})

                try:
                    guard_execution(self._control, browser)
                    candidate = self._wait_factory(
                        browser,
                        budget.wait_timeout(
                            self._settings.browser_timeout_seconds
                        ),
                    ).until(lambda driver: _first_clickable(driver, sort_requested=sort_requested, sort_selected=sort_selected))

                except TimeoutException:
                    wait_exhausted = budget.exhausted(attempts)
                    natural_complete = not wait_exhausted
                    break

                if not candidate:
                    natural_complete = True
                    break

                guard_execution(self._control, browser)
                try:
                    label = getattr(candidate, 'text', '').strip()
                    candidate.click()
                    if label in ('Most relevant', 'Phù hợp nhất', 'Relevant', 'Newest', 'Mới nhất'):
                        sort_requested = True
                    elif label.startswith(('All comments', 'Tất cả bình luận')):
                        sort_selected = True
                except StaleElementReferenceException:
                    continue

                if delay_seconds:
                    if not cooperative_wait(
                        delay_seconds,
                        control=self._control,
                        sleep=self._sleep,
                        monotonic=self._monotonic,
                        deadline_monotonic=budget.deadline_monotonic,
                    ):
                        wait_exhausted = True
                        break

            guard_execution(self._control, browser)
            return CrawlCollection(
                str(browser.page_source),
                attempts,
                budget_exhausted=(
                    wait_exhausted
                    or (not natural_complete and budget.exhausted(attempts))
                ),
            )

        except (SessionError, RateLimitError, CrawlCancelled, JobBudgetReached, AccountSafetyStop):
            raise

        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated comments " "navigation failed.",
                target=url,
            ) from error
