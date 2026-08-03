from __future__ import annotations


import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from curl_cffi import requests

from fb_crawl.config import Settings
from fb_crawl.core.exceptions import FetchError


class HttpClient(Protocol):
    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str: ...


def _safe_target(url: str) -> str:
    parsed = urlsplit(url)

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
        )
    )


class CurlHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        requester: Callable[..., Any] = requests.get,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._requester = requester
        self._sleep = sleep_func

    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        request_headers = {
            "User-Agent": self._settings.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml," "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            **dict(headers or {}),
        }

        last_error: Exception | None = None

        for attempt in range(self._settings.max_retries + 1):
            try:
                response = self._requester(
                    url,
                    headers=request_headers,
                    timeout=self._settings.timeout_seconds,
                )
                response.raise_for_status()
                return str(response.text)

            except Exception as error:
                last_error = error

                if attempt < self._settings.max_retries:
                    delay = 0.25 * (2**attempt)
                    self._sleep(delay)

        safe_target = _safe_target(url)

        raise FetchError(
            f"Public fetch failed for {safe_target}.",
            target=safe_target,
        ) from last_error
