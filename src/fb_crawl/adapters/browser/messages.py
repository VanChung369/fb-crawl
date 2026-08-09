from __future__ import annotations

import time
from collections.abc import Callable

from fb_crawl.adapters.browser.crawl_budget import CrawlBudget
from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError


MESSAGE_SCROLL_SCRIPT = """
const main = document.querySelector('[role="main"]') || document.querySelector('main');
if (!main) return null;
const candidates = [main, ...main.querySelectorAll('*')];
const scroller = candidates.reduce((best, item) => {
  const overflow = item.scrollHeight - item.clientHeight;
  const bestOverflow = best.scrollHeight - best.clientHeight;
  return overflow > bestOverflow ? item : best;
}, main);
scroller.scrollTop = 0;
return scroller.scrollHeight;
"""

MESSAGE_CONTENT_SCRIPT = """
const main = document.querySelector('[role="main"]') || document.querySelector('main');
return main ? main.outerHTML : document.documentElement.outerHTML;
"""


class MessagesCollector:
    """Collect visible text from one explicitly requested conversation."""

    def __init__(
        self,
        settings: BrowserSettings,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
        sleep_func: Callable[[float], None] = time.sleep,
        monotonic_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._authenticated = authenticated_func
        self._ready = ready_func
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
            self._ready(browser, self._settings.browser_timeout_seconds)

            if not self._authenticated(browser):
                raise SessionError(
                    "The authenticated Facebook session is no longer valid."
                )

            attempts = 0
            previous: int | None = None
            budget = CrawlBudget(
                steps=steps,
                max_duration_seconds=max_duration_seconds,
                monotonic_func=self._monotonic,
            )

            while budget.allows(attempts):
                height = browser.execute_script(MESSAGE_SCROLL_SCRIPT)

                if height is None:
                    break

                attempts += 1

                if delay_seconds:
                    self._sleep(delay_seconds)

                current = int(height)
                if previous is not None and current <= previous:
                    break
                previous = current

            html = browser.execute_script(MESSAGE_CONTENT_SCRIPT)
            return str(html or browser.page_source), attempts

        except SessionError:
            raise
        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated messages navigation failed.", target=url
            ) from error
