from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from fb_crawl.adapters.browser.driver import (
    wait_for_document_ready,
    wait_for_profile_content,
)
from fb_crawl.adapters.browser.profile_parser import ProfileParser
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    SessionError,
)
from fb_crawl.core.models import ProfileDetails, ProfileField, UserRecord
from fb_crawl.core.urls import normalize_facebook_url, profile_directory_urls


def _ordered_union(first: tuple[str, ...], later: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *later)))


def _merge_details(first: ProfileDetails, later: ProfileDetails) -> ProfileDetails:
    return replace(
        first,
        phone_numbers=_ordered_union(first.phone_numbers, later.phone_numbers),
        phone_sources=_ordered_union(first.phone_sources, later.phone_sources),
        website=first.website or later.website,
        address=first.address or later.address,
        current_city=first.current_city or later.current_city,
        hometown=first.hometown or later.hometown,
        birth_date=first.birth_date or later.birth_date,
        birth_year=first.birth_year or later.birth_year,
        canonical_profile_url=(
            first.canonical_profile_url or later.canonical_profile_url
        ),
    )


def _profile_identity(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    parts = [part for part in parsed.path.split("/") if part]

    if len(parts) != 1:
        return ""

    if parts[0].casefold() == "profile.php":
        return parse_qs(parsed.query).get("id", [""])[0]

    return parts[0]


def _resolved_profile_url(
    browser,
    record: UserRecord,
    html: str,
) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates = [str(browser.current_url or "")]

    canonical = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )

    if canonical is not None:
        candidates.append(str(canonical.get("href") or ""))

    open_graph = soup.find("meta", attrs={"property": "og:url"})

    if open_graph is not None:
        candidates.append(str(open_graph.get("content") or ""))

    numeric_fallback: str | None = None

    for candidate in candidates:
        resolved = normalize_facebook_url(candidate)

        if resolved is None:
            continue

        resolved_identity = _profile_identity(resolved)

        if not resolved_identity:
            continue

        if record.user_id.isdigit():
            if resolved_identity.isdigit():
                if resolved_identity == record.user_id:
                    numeric_fallback = resolved
                continue

            return resolved

        if resolved_identity.casefold() == record.user_id.casefold():
            return resolved

    return numeric_fallback


class ProfileEnricher:
    def __init__(
        self,
        settings: BrowserSettings,
        parser: ProfileParser | None = None,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
        content_ready_func: Callable[
            [object, float, str],
            bool | None,
        ] = wait_for_profile_content,
    ) -> None:
        self._settings = settings
        self._parser = parser or ProfileParser()
        self._authenticated = authenticated_func
        self._ready = ready_func
        self._content_ready = content_ready_func

    def enrich(
        self,
        browser,
        record: UserRecord,
        fields: tuple[ProfileField, ...],
    ) -> ProfileDetails:
        routes = profile_directory_urls(record.profile_url, record.user_id)

        if not routes:
            raise BrowserNavigationError(
                "Authenticated profile target is unsupported.",
                target=record.profile_url,
            )

        details = ProfileDetails()
        succeeded = 0
        navigation_failures = 0
        parse_failures = 0
        critical_navigation_failure = False
        critical_parse_failure = False

        requested = frozenset(fields) if fields else frozenset(ProfileField)
        personal_requested = bool(requested - {ProfileField.WEBSITE})
        route_indexes = (
            *((0,) if personal_requested else ()),
            *((1,) if ProfileField.WEBSITE in requested else ()),
        )

        route_profile_url = record.profile_url
        route_identity = record.user_id

        for route_index in route_indexes:
            current_routes = profile_directory_urls(
                route_profile_url,
                route_identity,
            )

            if len(current_routes) != len(routes):
                navigation_failures += 1
                continue

            route = current_routes[route_index]

            try:
                browser.get(route)
                self._ready(browser, self._settings.browser_timeout_seconds)

                if not self._authenticated(browser):
                    raise SessionError(
                        "The authenticated Facebook session is no longer valid."
                    )

                self._content_ready(
                    browser,
                    self._settings.browser_timeout_seconds,
                    route,
                )

            except SessionError:
                raise

            except Exception:
                navigation_failures += 1
                critical_navigation_failure |= route_index == 0
                continue

            html = str(browser.page_source)
            resolved_profile_url = _resolved_profile_url(browser, record, html)

            if resolved_profile_url is not None:
                resolved_identity = _profile_identity(resolved_profile_url)

                if resolved_identity:
                    route_profile_url = resolved_profile_url
                    route_identity = resolved_identity

                if resolved_profile_url != record.profile_url:
                    details = replace(
                        details,
                        canonical_profile_url=resolved_profile_url,
                    )

            try:
                parsed = self._parser.parse(
                    html,
                    source_url=route,
                    requested_fields=fields,
                )

            except Exception:
                parse_failures += 1
                critical_parse_failure |= route_index == 0
                continue

            details = _merge_details(details, parsed)
            succeeded += 1

        if critical_parse_failure:
            raise BrowserParseError(
                "Authenticated profile parsing failed.",
                target=record.profile_url,
            )

        if critical_navigation_failure:
            raise BrowserNavigationError(
                "Authenticated profile navigation failed.",
                target=record.profile_url,
            )

        if succeeded:
            return details

        if parse_failures:
            raise BrowserParseError(
                "Authenticated profile parsing failed.",
                target=record.profile_url,
            )

        if navigation_failures:
            raise BrowserNavigationError(
                "Authenticated profile navigation failed.",
                target=record.profile_url,
            )

        return details
