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
    IdentityResolutionError,
    RateLimitError,
    SessionError,
)
from fb_crawl.core.identity import ascii_fold, is_suspicious_profile_name
from fb_crawl.core.models import ProfileIdentity, UserRecord
from fb_crawl.core.urls import FACEBOOK_HOSTS, FACEBOOK_INTERNAL_PATHS


NUMERIC_UID = re.compile(r"[1-9]\d{4,19}")
USERNAME = re.compile(r"[A-Za-z0-9._-]+")
UID_KEYS = ("userID", "id")
USERNAME_KEYS = (
    "userVanity",
    "username_for_profile",
    "username",
    "vanity",
)

RATE_LIMIT_MARKERS = (
    "you're temporarily blocked",
    "you’re temporarily blocked",
    "we limit how often",
    "temporarily blocked",
    "chung toi gioi han tan suat",
    "ban tam thoi bi chan",
)


def _numeric_uid(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if NUMERIC_UID.fullmatch(candidate) else None


def _username(value: object) -> str | None:
    candidate = str(value or "").strip()

    if (
        not candidate
        or candidate.isdigit()
        or USERNAME.fullmatch(candidate) is None
        or candidate.casefold() in FACEBOOK_INTERNAL_PATHS
    ):
        return None

    return candidate


def _url_identity(value: str) -> tuple[str | None, str | None]:
    parsed = urlparse(value)
    host = parsed.netloc.casefold().split(":")[0]

    if host not in FACEBOOK_HOSTS:
        return None, None

    parts = [part for part in parsed.path.split("/") if part]

    if len(parts) != 1:
        return None, None

    if parts[0].casefold() == "profile.php":
        return _numeric_uid(parse_qs(parsed.query).get("id", [""])[0]), None

    return None, _username(parts[0])


def _node_identities(node: dict) -> tuple[set[str], set[str]]:
    user_ids = {
        candidate
        for key in UID_KEYS
        if (candidate := _numeric_uid(node.get(key))) is not None
    }
    usernames = {
        candidate.casefold()
        for key in USERNAME_KEYS
        if (candidate := _username(node.get(key))) is not None
    }
    return user_ids, usernames


def _collect_identity(
    value: object,
    *,
    expected_user_id: str | None,
    expected_username: str | None,
    user_ids: set[str],
    names: set[str],
    usernames: dict[str, str],
) -> bool:
    matched = False

    if isinstance(value, dict):
        node_ids, node_usernames = _node_identities(value)
        expected_handle = (
            expected_username.casefold() if expected_username else None
        )
        node_matches = (
            expected_user_id is not None and expected_user_id in node_ids
        ) or (
            expected_handle is not None and expected_handle in node_usernames
        )

        if node_matches:
            matched = True
            user_ids.update(node_ids)

            for key in USERNAME_KEYS:
                candidate = _username(value.get(key))

                if candidate is not None:
                    usernames.setdefault(candidate.casefold(), candidate)

            candidate_name = str(value.get("name") or "").strip()

            if not is_suspicious_profile_name(candidate_name):
                names.add(candidate_name)

        for child in value.values():
            matched = (
                _collect_identity(
                    child,
                    expected_user_id=expected_user_id,
                    expected_username=expected_username,
                    user_ids=user_ids,
                    names=names,
                    usernames=usernames,
                )
                or matched
            )

    elif isinstance(value, list):
        for child in value:
            matched = (
                _collect_identity(
                    child,
                    expected_user_id=expected_user_id,
                    expected_username=expected_username,
                    user_ids=user_ids,
                    names=names,
                    usernames=usernames,
                )
                or matched
            )

    return matched


def _is_rate_limited(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    surface = soup.body or soup
    text = " ".join(surface.stripped_strings)
    folded = " ".join(ascii_fold(text).split())
    return any(marker in folded for marker in RATE_LIMIT_MARKERS)


class ProfileIdentityParser:
    def parse(
        self,
        html: str,
        *,
        expected_user_id: str | None = None,
        expected_username: str | None = None,
        current_url: str = "",
    ) -> ProfileIdentity | None:
        expected_uid = _numeric_uid(expected_user_id)
        expected_handle = _username(expected_username)

        if expected_uid is None and expected_handle is None:
            return None

        current_uid, current_handle = _url_identity(current_url)

        if expected_uid is None and current_uid is not None:
            expected_uid = current_uid

        soup = BeautifulSoup(html, "html.parser")
        user_ids: set[str] = set()
        names: set[str] = set()
        usernames: dict[str, str] = {}
        matched = False

        for script in soup.find_all("script"):
            raw = script.string or script.get_text()

            if not raw:
                continue

            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            matched = (
                _collect_identity(
                    payload,
                    expected_user_id=expected_uid,
                    expected_username=expected_handle,
                    user_ids=user_ids,
                    names=names,
                    usernames=usernames,
                )
                or matched
            )

        if not matched:
            return None

        if expected_uid is not None:
            user_ids.add(expected_uid)

        if len(user_ids) != 1:
            return None

        if current_handle is not None:
            usernames.setdefault(current_handle.casefold(), current_handle)

        if expected_handle is not None:
            usernames.setdefault(expected_handle.casefold(), expected_handle)

        if len(usernames) > 1:
            return None

        user_id = next(iter(user_ids))
        username = next(iter(usernames.values()), None)
        name = max(names, key=len) if names else None
        profile_url = (
            f"https://www.facebook.com/{username}"
            if username is not None
            else f"https://www.facebook.com/profile.php?id={user_id}"
        )
        return ProfileIdentity(
            user_id=user_id,
            name=name,
            username=username,
            profile_url=profile_url,
        )


class ProfileIdentityResolver:
    def __init__(
        self,
        settings: BrowserSettings,
        parser: ProfileIdentityParser | None = None,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
    ) -> None:
        self._settings = settings
        self._parser = parser or ProfileIdentityParser()
        self._authenticated = authenticated_func
        self._ready = ready_func

    def resolve(self, browser, record: UserRecord) -> ProfileIdentity:
        try:
            browser.get(record.profile_url)
            self._ready(browser, self._settings.browser_timeout_seconds)

            if not self._authenticated(browser):
                raise SessionError(
                    "The authenticated Facebook session is no longer valid."
                )

        except (SessionError, RateLimitError):
            raise

        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated profile navigation failed.",
                target=record.profile_url,
            ) from error

        html = str(browser.page_source)

        if _is_rate_limited(html):
            raise RateLimitError(
                "Facebook temporarily limited authenticated profile requests.",
                target=record.profile_url,
            )

        identity = self._parser.parse(
            html,
            expected_user_id=(record.user_id if record.user_id.isdigit() else None),
            expected_username=(
                record.username
                or (record.user_id if not record.user_id.isdigit() else None)
            ),
            current_url=str(browser.current_url or ""),
        )

        if identity is None:
            raise IdentityResolutionError(
                "Authenticated profile identity resolution failed.",
                target=record.profile_url,
            )

        return identity
