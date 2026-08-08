from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.profile_parser import ProfileParser
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    SessionError,
)
from fb_crawl.core.models import ProfileDetails, ProfileField, UserRecord
from fb_crawl.core.urls import profile_about_urls


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
    )


class ProfileEnricher:
    def __init__(
        self,
        settings: BrowserSettings,
        parser: ProfileParser | None = None,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
    ) -> None:
        self._settings = settings
        self._parser = parser or ProfileParser()
        self._authenticated = authenticated_func
        self._ready = ready_func

    def enrich(
        self,
        browser,
        record: UserRecord,
        fields: tuple[ProfileField, ...],
    ) -> ProfileDetails:
        routes = profile_about_urls(record.profile_url, record.user_id)

        if not routes:
            raise BrowserNavigationError(
                "Authenticated profile target is unsupported.",
                target=record.profile_url,
            )

        details = ProfileDetails()
        succeeded = 0
        navigation_failures = 0
        parse_failures = 0

        for route in routes:
            try:
                browser.get(route)
                self._ready(browser, self._settings.browser_timeout_seconds)

                if not self._authenticated(browser):
                    raise SessionError(
                        "The authenticated Facebook session is no longer valid."
                    )

            except SessionError:
                raise

            except Exception:
                navigation_failures += 1
                continue

            try:
                parsed = self._parser.parse(
                    str(browser.page_source),
                    source_url=route,
                    requested_fields=fields,
                )

            except Exception:
                parse_failures += 1
                continue

            details = _merge_details(details, parsed)
            succeeded += 1

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
