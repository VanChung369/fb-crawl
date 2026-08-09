from __future__ import annotations

from collections.abc import Callable

from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    RateLimitError,
    SessionError,
)
from fb_crawl.core.models import InspectRecord
from fb_crawl.core.urls import classify_authenticated_url


INSPECT_SCRIPT = r"""
const visible = element => Boolean(
  element && (element.offsetWidth || element.offsetHeight ||
  element.getClientRects().length)
);
const profilePattern = /facebook\.com\/(?:profile\.php\?id=|user\/|[A-Za-z0-9._-]+\/?$)/i;
const profileLinks = new Set(
  Array.from(document.querySelectorAll('a[href]'))
    .filter(visible)
    .map(anchor => anchor.href || '')
    .filter(href => profilePattern.test(href))
);
const labels = [
  'birthday', 'current city', 'hometown', 'mobile', 'phone', 'address',
  'website', 'bio', 'workplace', 'education', 'gender', 'languages',
  'relationship status', 'ngày sinh', 'thành phố hiện tại', 'quê quán',
  'điện thoại', 'địa chỉ', 'tiểu sử', 'công việc', 'học vấn', 'giới tính',
  'ngôn ngữ', 'tình trạng mối quan hệ'
];
const fold = value => (value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
const fieldLabels = Array.from(document.querySelectorAll('span,div,h1,h2,h3,h4'))
  .filter(visible)
  .filter(element => labels.includes(fold(element.textContent))).length;
return {
  document_ready: document.readyState === 'complete',
  main_found: Boolean(document.querySelector('[role="main"], main')),
  dialog_count: Array.from(document.querySelectorAll('[role="dialog"]'))
    .filter(visible).length,
  visible_profile_links: profileLinks.size,
  message_rows: document.querySelectorAll(
    '[data-message-id], [data-testid="message-container"], [role="row"]'
  ).length,
  profile_field_labels: fieldLabels
};
"""


class BrowserInspector:
    def __init__(
        self,
        settings: BrowserSettings,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
    ) -> None:
        self._settings = settings
        self._authenticated = authenticated_func
        self._ready = ready_func

    def inspect(self, browser, url: str) -> InspectRecord:
        try:
            browser.get(url)
            self._ready(browser, self._settings.browser_timeout_seconds)
            session_valid = self._authenticated(browser)

            if not session_valid:
                raise SessionError(
                    "The authenticated Facebook session is no longer valid."
                )

            values = browser.execute_script(INSPECT_SCRIPT) or {}
            classified = classify_authenticated_url(url)
            target_action = classified[0].value if classified else "unknown"
            return InspectRecord(
                target_url=url,
                target_action=target_action,
                session_valid=True,
                document_ready=bool(values.get("document_ready")),
                main_found=bool(values.get("main_found")),
                dialog_count=int(values.get("dialog_count") or 0),
                visible_profile_links=int(
                    values.get("visible_profile_links") or 0
                ),
                message_rows=int(values.get("message_rows") or 0),
                profile_field_labels=int(
                    values.get("profile_field_labels") or 0
                ),
            )

        except (SessionError, RateLimitError):
            raise
        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated inspection failed.", target=url
            ) from error
