from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    SessionError,
    UidResolutionError,
)
from fb_crawl.core.models import UserRecord


NUMERIC_UID = re.compile(r"[1-9]\d{4,19}")
USERNAME_KEYS = (
    "userVanity",
    "username",
    "username_for_profile",
    "vanity",
)


def _numeric_uid(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if NUMERIC_UID.fullmatch(candidate) else None


def _numeric_profile_url(value: str) -> str | None:
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]

    if len(parts) != 1 or parts[0].casefold() != "profile.php":
        return None

    return _numeric_uid(parse_qs(parsed.query).get("id", [""])[0])


def _matching_uid(node: dict, expected_username: str) -> str | None:
    matched_keys = tuple(
        key
        for key in USERNAME_KEYS
        if isinstance(node.get(key), str)
        and str(node[key]).casefold() == expected_username.casefold()
    )

    if not matched_keys:
        return None

    direct = _numeric_uid(node.get("userID"))

    if direct is not None:
        return direct

    if any(
        key in matched_keys
        for key in ("username", "username_for_profile", "vanity")
    ):
        return _numeric_uid(node.get("id"))

    return None


def _collect_matching_uids(
    value: object,
    expected_username: str,
    candidates: set[str],
) -> None:
    if isinstance(value, dict):
        candidate = _matching_uid(value, expected_username)

        if candidate is not None:
            candidates.add(candidate)

        for child in value.values():
            _collect_matching_uids(child, expected_username, candidates)

    elif isinstance(value, list):
        for child in value:
            _collect_matching_uids(child, expected_username, candidates)


class ProfileUidParser:
    def parse(self, html: str, *, expected_username: str) -> str | None:
        expected = expected_username.strip()

        if not expected:
            return None

        soup = BeautifulSoup(html, "html.parser")
        candidates: set[str] = set()

        for tag in soup.find_all("link", href=True):
            relation = tag.get("rel") or ()

            if any(str(item).casefold() == "canonical" for item in relation):
                candidate = _numeric_profile_url(str(tag.get("href") or ""))

                if candidate is not None:
                    candidates.add(candidate)

        for tag in soup.find_all("meta"):
            if str(tag.get("property") or "").casefold() != "og:url":
                continue

            candidate = _numeric_profile_url(str(tag.get("content") or ""))

            if candidate is not None:
                candidates.add(candidate)

        for script in soup.find_all("script"):
            raw = script.string or script.get_text()

            if not raw or expected.casefold() not in raw.casefold():
                continue

            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            _collect_matching_uids(payload, expected, candidates)

        if len(candidates) != 1:
            return None

        return next(iter(candidates))


class ProfileUidResolver:
    def __init__(
        self,
        settings: BrowserSettings,
        parser: ProfileUidParser | None = None,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
    ) -> None:
        self._settings = settings
        self._parser = parser or ProfileUidParser()
        self._authenticated = authenticated_func
        self._ready = ready_func

    def resolve(self, browser, record: UserRecord) -> str:
        if record.user_id.isdigit():
            return record.user_id

        try:
            browser.get(record.profile_url)
            self._ready(browser, self._settings.browser_timeout_seconds)

            if not self._authenticated(browser):
                raise SessionError(
                    "The authenticated Facebook session is no longer valid."
                )

        except SessionError:
            raise

        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated profile navigation failed.",
                target=record.profile_url,
            ) from error

        redirected_uid = _numeric_profile_url(str(browser.current_url or ""))

        if redirected_uid is not None:
            return redirected_uid

        resolved = self._parser.parse(
            str(browser.page_source),
            expected_username=(record.username or record.user_id),
        )

        if resolved is None:
            raise UidResolutionError(
                "Authenticated UID resolution failed.",
                target=record.profile_url,
            )

        return resolved
