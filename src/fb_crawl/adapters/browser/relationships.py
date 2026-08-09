from __future__ import annotations

import random
import time
from collections.abc import Callable

from fb_crawl.adapters.browser.crawl_budget import CrawlBudget
from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    RateLimitError,
    SessionError,
)


RELATIONSHIP_CONTENT_SCRIPT = """
const main = document.querySelector('[role="main"]') || document.querySelector('main');
return main ? main.outerHTML : document.documentElement.outerHTML;
"""


class RelationshipCollector:
    """Collect the visible portion of a friends or followers page."""

    def __init__(
        self,
        settings: BrowserSettings,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
        sleep_func: Callable[[float], None] = time.sleep,
        jitter_func: Callable[[float, float], float] = random.uniform,
        monotonic_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._authenticated = authenticated_func
        self._ready = ready_func
        self._sleep = sleep_func
        self._jitter = jitter_func
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
            self._ready(browser, self._settings.browser_timeout_seconds)

            if not self._authenticated(browser):
                raise SessionError(
                    "The authenticated Facebook session is no longer valid."
                )

            previous = int(
                browser.execute_script("return document.body.scrollHeight")
            )
            attempts = 0
            budget = CrawlBudget(
                steps=steps,
                max_duration_seconds=max_duration_seconds,
                monotonic_func=self._monotonic,
            )

            while budget.allows(attempts):
                browser.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                attempts += 1
                jitter_limit = min(delay_seconds * 0.15, 0.5)
                self._sleep(
                    delay_seconds + self._jitter(0.0, jitter_limit)
                )
                current = int(
                    browser.execute_script("return document.body.scrollHeight")
                )

                if current <= previous:
                    break

                previous = current

            html = browser.execute_script(RELATIONSHIP_CONTENT_SCRIPT)
            return str(html or browser.page_source), attempts

        except (SessionError, RateLimitError):
            raise

        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated relationship navigation failed.",
                target=url,
            ) from error
