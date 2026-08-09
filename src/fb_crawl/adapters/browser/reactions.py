from __future__ import annotations

import time
from collections.abc import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from fb_crawl.adapters.browser.crawl_budget import CrawlBudget
from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError


REACTIONS_XPATH = (
    "//*[(@role='button' or self::button or self::a) and ("
    "contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
    "'abcdefghijklmnopqrstuvwxyz'), 'reaction') or "
    "contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
    "'abcdefghijklmnopqrstuvwxyz'), 'reaction') or "
    "contains(@aria-label, 'Cảm xúc') or contains(normalize-space(.), 'cảm xúc') or "
    "contains(@href, 'reaction'))]"
)

REACTIONS_SCROLL_SCRIPT = """
const dialog = document.querySelector('[role="dialog"]');
if (!dialog) return null;
const candidates = [dialog, ...dialog.querySelectorAll('*')];
const scroller = candidates.reduce((best, item) => {
  const overflow = item.scrollHeight - item.clientHeight;
  const bestOverflow = best.scrollHeight - best.clientHeight;
  return overflow > bestOverflow ? item : best;
}, dialog);
scroller.scrollTop = scroller.scrollHeight;
return scroller.scrollHeight;
"""

REACTIONS_CONTENT_SCRIPT = """
const dialog = document.querySelector('[role="dialog"]');
return dialog ? dialog.outerHTML : null;
"""


def _clickable_reactions(browser):
    for element in browser.find_elements(By.XPATH, REACTIONS_XPATH):
        if element.is_displayed() and element.is_enabled():
            return element
    return False


def _visible_dialog(browser):
    for element in browser.find_elements(By.CSS_SELECTOR, '[role="dialog"]'):
        if element.is_displayed():
            return element
    return False


class ReactionsCollector:
    """Open a post's visible reactions dialog and collect its loaded users."""

    def __init__(
        self,
        settings: BrowserSettings,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
        wait_factory=WebDriverWait,
        sleep_func: Callable[[float], None] = time.sleep,
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
            self._ready(browser, self._settings.browser_timeout_seconds)

            if not self._authenticated(browser):
                raise SessionError(
                    "The authenticated Facebook session is no longer valid."
                )

            candidate = self._wait_factory(
                browser, self._settings.browser_timeout_seconds
            ).until(_clickable_reactions)
            candidate.click()
            self._wait_factory(
                browser, self._settings.browser_timeout_seconds
            ).until(_visible_dialog)

            attempts = 0
            previous: int | None = None
            budget = CrawlBudget(
                steps=steps,
                max_duration_seconds=max_duration_seconds,
                monotonic_func=self._monotonic,
            )

            while budget.allows(attempts):
                height = browser.execute_script(REACTIONS_SCROLL_SCRIPT)

                if height is None:
                    break

                attempts += 1

                if delay_seconds:
                    self._sleep(delay_seconds)

                current = int(height)
                if previous is not None and current <= previous:
                    break
                previous = current

            html = browser.execute_script(REACTIONS_CONTENT_SCRIPT)

            if not html:
                raise BrowserNavigationError(
                    "Visible reactions list could not be opened.", target=url
                )

            return str(html), attempts

        except SessionError:
            raise
        except BrowserNavigationError:
            raise
        except TimeoutException as error:
            raise BrowserNavigationError(
                "Visible reactions list could not be opened.", target=url
            ) from error
        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated reactions navigation failed.", target=url
            ) from error
