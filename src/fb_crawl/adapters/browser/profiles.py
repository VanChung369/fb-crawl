from __future__ import annotations

import re
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
    RateLimitError,
    SessionError,
)
from fb_crawl.core.models import (
    FieldStatus,
    ProfileDetails,
    ProfileField,
    UserRecord,
)
from fb_crawl.core.urls import (
    PROFILE_FIELD_SECTIONS,
    normalize_facebook_url,
    profile_enrichment_urls,
)


def _ordered_union(first: tuple[str, ...], later: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *later)))


def _merge_details(first: ProfileDetails, later: ProfileDetails) -> ProfileDetails:
    return replace(
        first,
        name=first.name or later.name,
        phone_numbers=_ordered_union(first.phone_numbers, later.phone_numbers),
        phone_sources=_ordered_union(first.phone_sources, later.phone_sources),
        website=first.website or later.website,
        address=first.address or later.address,
        current_city=first.current_city or later.current_city,
        hometown=first.hometown or later.hometown,
        birth_date=first.birth_date or later.birth_date,
        birth_year=first.birth_year or later.birth_year,
        bio=first.bio or later.bio,
        workplace=first.workplace or later.workplace,
        education=first.education or later.education,
        gender=first.gender or later.gender,
        languages=_ordered_union(first.languages, later.languages),
        relationship_status=(
            first.relationship_status or later.relationship_status
        ),
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


def _browser_profile_name(browser) -> str | None:
    title = " ".join(str(getattr(browser, "title", "") or "").split())
    title = re.sub(r"^\(\d+\)\s*", "", title)

    for suffix in (" | Facebook", " - Facebook"):
        if title.casefold().endswith(suffix.casefold()):
            title = title[: -len(suffix)].strip()
            break

    if not title or title.casefold() == "facebook" or len(title) > 200:
        return None

    return title


def _route_section(route: str) -> str:
    parsed = urlparse(route)
    parts = [part for part in parsed.path.split("/") if part]

    if len(parts) == 1 and parts[0].casefold() == "profile.php":
        return parse_qs(parsed.query).get("sk", [""])[0].casefold()

    return parts[-1].casefold() if parts else ""


def _field_value(details: ProfileDetails, field: ProfileField):
    values = {
        ProfileField.PHONE: details.phone_numbers,
        ProfileField.WEBSITE: details.website,
        ProfileField.ADDRESS: details.address,
        ProfileField.CURRENT_CITY: details.current_city,
        ProfileField.HOMETOWN: details.hometown,
        ProfileField.BIRTH_DATE: details.birth_date or details.birth_year,
        ProfileField.BIO: details.bio,
        ProfileField.WORKPLACE: details.workplace,
        ProfileField.EDUCATION: details.education,
        ProfileField.GENDER: details.gender,
        ProfileField.LANGUAGES: details.languages,
        ProfileField.RELATIONSHIP_STATUS: details.relationship_status,
    }
    return values[field]


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
        routes = profile_enrichment_urls(
            record.profile_url,
            record.user_id,
            fields,
        )

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
        unavailable_sections: set[str] = set()
        failed_sections: set[str] = set()

        requested = tuple(fields) if fields else tuple(ProfileField)
        requested_set = frozenset(requested)

        route_profile_url = record.profile_url
        route_identity = record.user_id

        for route_index, _ in enumerate(routes):
            current_routes = profile_enrichment_urls(
                route_profile_url,
                route_identity,
                fields,
            )

            if len(current_routes) != len(routes):
                navigation_failures += 1
                failed_sections.add(_route_section(routes[route_index]))
                continue

            route = current_routes[route_index]
            section = _route_section(route)

            try:
                browser.get(route)
                self._ready(browser, self._settings.browser_timeout_seconds)

                if not self._authenticated(browser):
                    raise SessionError(
                        "The authenticated Facebook session is no longer valid."
                    )

                content_ready = self._content_ready(
                    browser,
                    self._settings.browser_timeout_seconds,
                    route,
                )

                if content_ready is False:
                    unavailable_sections.add(section)

            except (SessionError, RateLimitError):
                raise

            except Exception:
                navigation_failures += 1
                failed_sections.add(section)
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

                if parsed.name is None:
                    parsed = replace(
                        parsed,
                        name=_browser_profile_name(browser),
                    )

            except Exception:
                parse_failures += 1
                unavailable_sections.add(section)
                critical_parse_failure |= route_index == 0
                continue

            details = _merge_details(details, parsed)
            succeeded += 1

        if ProfileField.PHONE in requested_set and route_profile_url:
            try:
                browser.get(route_profile_url)
                self._ready(browser, self._settings.browser_timeout_seconds)

                if not self._authenticated(browser):
                    raise SessionError(
                        "The authenticated Facebook session is no longer valid."
                    )

                self._content_ready(
                    browser,
                    self._settings.browser_timeout_seconds,
                    route_profile_url,
                )
                timeline_details = self._parser.parse(
                    str(browser.page_source),
                    source_url=route_profile_url,
                    requested_fields=(ProfileField.PHONE,),
                )
                details = _merge_details(details, timeline_details)

            except (SessionError, RateLimitError):
                raise

            except Exception:
                # Timeline scanning is supplementary. Dedicated profile fields
                # remain valid when the visible feed is unavailable.
                pass

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
            statuses: list[tuple[str, str]] = []
            sources: list[tuple[str, str]] = []

            for field in ProfileField:
                section = PROFILE_FIELD_SECTIONS[field]

                if field not in requested_set:
                    status = FieldStatus.NOT_REQUESTED
                elif _field_value(details, field):
                    status = FieldStatus.FOUND
                    field_source = (
                        ";".join(details.phone_sources)
                        if field is ProfileField.PHONE
                        and details.phone_sources
                        else f"facebook:{section}"
                    )
                    sources.append(
                        (field.value, field_source)
                    )
                elif section in failed_sections:
                    status = FieldStatus.NAVIGATION_FAILED
                elif section in unavailable_sections:
                    status = FieldStatus.SECTION_UNAVAILABLE
                else:
                    status = FieldStatus.NOT_VISIBLE

                statuses.append((field.value, status.value))

            return replace(
                details,
                field_status=tuple(statuses),
                field_sources=tuple(sources),
            )

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
