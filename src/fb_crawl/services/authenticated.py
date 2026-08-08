from __future__ import annotations

from typing import Protocol
from dataclasses import replace

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
    )


class AuthenticatedService:
    def __init__(
        self,
        session: SessionPort,
        members: CollectionPort,
        comments: CollectionPort,
        parser: UserParserPort,
    ) -> None:
        self._session = session
        self._members = members
        self._comments = comments
        self._parser = parser

    def validate(
        self,
        request: ScrapeRequest,
    ) -> None:
        _prepared_targets(request)

    def run(
        self,
        request: ScrapeRequest,
        browser,
    ) -> ScrapeResult[UserRecord]:
        prepared, issues = _prepared_targets(request)

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
        )
