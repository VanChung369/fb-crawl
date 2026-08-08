from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from urllib.parse import (
    urlsplit,
    urlunsplit,
)

from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    SessionError,
    ValidationError,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
    EnrichmentStats,
    ProfileDetails,
    ProfileField,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.core.urls import (
    classify_authenticated_url,
    normalize_comments_url,
    normalize_members_url,
)


class SessionPort(Protocol):
    def ensure_authenticated(
        self,
        browser,
    ) -> None: ...

    def assert_authenticated(
        self,
        browser,
    ) -> None: ...


class CollectionPort(Protocol):
    def collect(
        self,
        browser,
        url: str,
        *,
        steps: int,
        delay_seconds: float,
    ) -> tuple[str, int]: ...


class UserParserPort(Protocol):
    def parse(
        self,
        html: str,
        *,
        source: str,
        source_url: str,
    ) -> tuple[UserRecord, ...]: ...


class ProfileEnricherPort(Protocol):
    def enrich(
        self,
        browser,
        record: UserRecord,
        fields: tuple[ProfileField, ...],
    ) -> ProfileDetails: ...


PreparedTarget = tuple[
    AuthenticatedAction,
    str,
]


def _safe_target(value: str) -> str:
    parsed = urlsplit(value)

    if parsed.scheme and parsed.hostname:
        return urlunsplit(
            (
                parsed.scheme,
                parsed.hostname,
                parsed.path,
                "",
                "",
            )
        )

    return parsed.path


def _prepared_targets(
    request: ScrapeRequest,
) -> tuple[
    list[PreparedTarget],
    list[ScrapeIssue],
]:
    if request.mode is not ScrapeMode.AUTHENTICATED:
        raise ValidationError("AuthenticatedService requires " "authenticated mode.")

    try:
        action = AuthenticatedAction(request.action)
    except ValueError as error:
        raise ValidationError("Unsupported authenticated action.") from error

    if not request.targets:
        raise ValidationError("At least one authenticated " "target is required.")

    prepared: list[PreparedTarget] = []
    issues: list[ScrapeIssue] = []

    for raw in request.targets:
        if action is AuthenticatedAction.MEMBERS:
            normalized = normalize_members_url(raw)

            if normalized is None:
                raise ValidationError("An unsupported members " "target was provided.")

            prepared.append(
                (
                    action,
                    normalized,
                )
            )

        elif action is AuthenticatedAction.COMMENTS:
            normalized = normalize_comments_url(raw)

            if normalized is None:
                raise ValidationError("An unsupported comments " "target was provided.")

            prepared.append(
                (
                    action,
                    normalized,
                )
            )

        else:
            classified = classify_authenticated_url(raw)

            if classified is None:
                issues.append(
                    ScrapeIssue(
                        code=ValidationError.code,
                        message=("Unsupported authenticated " "batch target."),
                        target=_safe_target(raw),
                        mode=(ScrapeMode.AUTHENTICATED),
                        action=(AuthenticatedAction.BATCH.value),
                    )
                )

            else:
                prepared.append(classified)

    return prepared, issues


def _merge_record(
    first: UserRecord,
    later: UserRecord,
) -> UserRecord:
    return replace(
        first,
        name=first.name or later.name,
        profile_url=(first.profile_url or later.profile_url),
        phone_numbers=tuple(
            dict.fromkeys((*first.phone_numbers, *later.phone_numbers))
        ),
        phone_sources=tuple(
            dict.fromkeys((*first.phone_sources, *later.phone_sources))
        ),
        website=first.website or later.website,
        address=first.address or later.address,
        current_city=first.current_city or later.current_city,
        hometown=first.hometown or later.hometown,
        birth_date=first.birth_date or later.birth_date,
        birth_year=first.birth_year or later.birth_year,
    )


def _merge_details(record: UserRecord, details: ProfileDetails) -> UserRecord:
    return replace(
        record,
        profile_url=(details.canonical_profile_url or record.profile_url),
        phone_numbers=tuple(
            dict.fromkeys((*record.phone_numbers, *details.phone_numbers))
        ),
        phone_sources=tuple(
            dict.fromkeys((*record.phone_sources, *details.phone_sources))
        ),
        website=record.website or details.website,
        address=record.address or details.address,
        current_city=record.current_city or details.current_city,
        hometown=record.hometown or details.hometown,
        birth_date=record.birth_date or details.birth_date,
        birth_year=record.birth_year or details.birth_year,
    )


class AuthenticatedService:
    def __init__(
        self,
        session: SessionPort,
        members: CollectionPort,
        comments: CollectionPort,
        parser: UserParserPort,
        profile_enricher: ProfileEnricherPort | None = None,
        *,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session
        self._members = members
        self._comments = comments
        self._parser = parser
        self._profile_enricher = profile_enricher
        self._sleep = sleep_func

    def validate(
        self,
        request: ScrapeRequest,
    ) -> None:
        _prepared_targets(request)

        if request.enrich_profiles and self._profile_enricher is None:
            raise ValidationError("Profile enrichment runtime is unavailable.")

    def run(
        self,
        request: ScrapeRequest,
        browser,
    ) -> ScrapeResult[UserRecord]:
        prepared, issues = _prepared_targets(request)

        if request.enrich_profiles and self._profile_enricher is None:
            raise ValidationError("Profile enrichment runtime is unavailable.")

        if prepared:
            self._session.ensure_authenticated(browser)

        records_by_id: dict[
            str,
            UserRecord,
        ] = {}
        discovered = 0

        for action, url in prepared:
            self._session.assert_authenticated(browser)

            collector = (
                self._members
                if action is AuthenticatedAction.MEMBERS
                else self._comments
            )

            try:
                html, _ = collector.collect(
                    browser,
                    url,
                    steps=request.steps,
                    delay_seconds=(request.delay_seconds),
                )

                try:
                    parsed = self._parser.parse(
                        html,
                        source=action.value,
                        source_url=url,
                    )

                except BrowserParseError:
                    raise

                except Exception as error:
                    raise BrowserParseError(
                        "Authenticated user " "parsing failed.",
                        target=url,
                    ) from error

                discovered += len(parsed)
                for record in parsed:
                    existing = records_by_id.get(record.user_id)

                    records_by_id[record.user_id] = (
                        record
                        if existing is None
                        else _merge_record(
                            existing,
                            record,
                        )
                    )

            except SessionError:
                # Session loss is a fatal error for the entire batch, so we re-raise it to indicate that the session is no longer valid and cannot be trusted.
                raise

            except (
                BrowserNavigationError,
                BrowserParseError,
            ) as error:
                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=(error.target or url),
                        mode=(ScrapeMode.AUTHENTICATED),
                        action=action.value,
                    )
                )

        enrichment: EnrichmentStats | None = None

        if request.enrich_profiles:
            selected_ids = tuple(records_by_id)[: request.profile_limit]
            fields = request.profile_fields or tuple(ProfileField)
            attempted = 0
            enrichment_succeeded = 0
            enrichment_failed = 0
            phone_found = 0
            address_found = 0
            current_city_found = 0
            hometown_found = 0
            birth_year_found = 0

            for index, user_id in enumerate(selected_ids):
                record = records_by_id[user_id]
                attempted += 1

                try:
                    details = self._profile_enricher.enrich(
                        browser,
                        record,
                        fields,
                    )

                except SessionError:
                    raise

                except (
                    BrowserNavigationError,
                    BrowserParseError,
                ) as error:
                    enrichment_failed += 1
                    issues.append(
                        ScrapeIssue(
                            code=error.code,
                            message=error.safe_message,
                            target=(error.target or record.profile_url),
                            mode=ScrapeMode.AUTHENTICATED,
                            action="profile_enrichment",
                        )
                    )

                except Exception:
                    enrichment_failed += 1
                    safe_error = BrowserParseError(
                        "Authenticated profile parsing failed.",
                        target=record.profile_url,
                    )
                    issues.append(
                        ScrapeIssue(
                            code=safe_error.code,
                            message=safe_error.safe_message,
                            target=record.profile_url,
                            mode=ScrapeMode.AUTHENTICATED,
                            action="profile_enrichment",
                        )
                    )

                else:
                    records_by_id[user_id] = _merge_details(record, details)
                    enrichment_succeeded += 1
                    phone_found += bool(details.phone_numbers)
                    address_found += bool(details.address)
                    current_city_found += bool(details.current_city)
                    hometown_found += bool(details.hometown)
                    birth_year_found += details.birth_year is not None

                if index + 1 < len(selected_ids) and request.profile_delay_seconds:
                    self._sleep(request.profile_delay_seconds)

            enrichment = EnrichmentStats(
                selected=len(selected_ids),
                attempted=attempted,
                succeeded=enrichment_succeeded,
                failed=enrichment_failed,
                phone_found=phone_found,
                address_found=address_found,
                current_city_found=current_city_found,
                hometown_found=hometown_found,
                birth_year_found=birth_year_found,
            )

        records = tuple(records_by_id.values())

        return ScrapeResult(
            records=records,
            issues=tuple(issues),
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=discovered,
                succeeded=len(records),
                failed=len(issues),
            ),
            enrichment=enrichment,
        )
