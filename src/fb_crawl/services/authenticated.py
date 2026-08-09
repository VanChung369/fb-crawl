from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
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
    AuthenticatedBatchResult,
    EnrichmentStats,
    FieldStatus,
    InspectRecord,
    MessageRecord,
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
    classify_authenticated_batch_target,
    classify_inspect_target,
    normalize_comments_url,
    normalize_members_url,
    normalize_messages_url,
    normalize_profile_collection_url,
    normalize_reactions_url,
    profile_identity_url,
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


class MessageParserPort(Protocol):
    def parse(
        self,
        html: str,
        *,
        source_url: str,
    ) -> tuple[MessageRecord, ...]: ...


class InspectorPort(Protocol):
    def inspect(self, browser, url: str) -> InspectRecord: ...


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

    if action is AuthenticatedAction.MESSAGES and request.enrich_profiles:
        raise ValidationError(
            "Profile enrichment options are not supported by messages."
        )

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

        elif action is AuthenticatedAction.PROFILE:
            identity = profile_identity_url(raw)

            if identity is None:
                raise ValidationError("An unsupported profile target was provided.")

            prepared.append((action, identity[1]))

        elif action in {
            AuthenticatedAction.FRIENDS,
            AuthenticatedAction.FOLLOWERS,
        }:
            normalized = normalize_profile_collection_url(raw, action.value)

            if normalized is None:
                raise ValidationError(
                    f"An unsupported {action.value} target was provided."
                )

            prepared.append((action, normalized))

        elif action in {
            AuthenticatedAction.REACTIONS,
            AuthenticatedAction.ENGAGEMENT,
        }:
            normalized = normalize_reactions_url(raw)

            if normalized is None:
                raise ValidationError(
                    f"An unsupported {action.value} target was provided."
                )

            prepared.append((action, normalized))

        elif action is AuthenticatedAction.MESSAGES:
            normalized = normalize_messages_url(raw)

            if normalized is None:
                raise ValidationError(
                    "Messages require an explicit supported conversation URL."
                )

            prepared.append((action, normalized))

        elif action is AuthenticatedAction.INSPECT:
            classified = classify_inspect_target(raw)

            if classified is None:
                raise ValidationError("An unsupported inspect target was provided.")

            prepared.append((action, classified[1]))

        else:
            classified = classify_authenticated_batch_target(raw)

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
        bio=first.bio or later.bio,
        workplace=first.workplace or later.workplace,
        education=first.education or later.education,
        gender=first.gender or later.gender,
        languages=tuple(dict.fromkeys((*first.languages, *later.languages))),
        relationship_status=(
            first.relationship_status or later.relationship_status
        ),
        field_status=tuple(
            {
                **dict(first.field_status),
                **dict(later.field_status),
            }.items()
        ),
        field_sources=tuple(
            {
                **dict(first.field_sources),
                **dict(later.field_sources),
            }.items()
        ),
        first_seen=first.first_seen or later.first_seen,
        last_seen=later.last_seen or first.last_seen,
        last_enriched_at=(later.last_enriched_at or first.last_enriched_at),
        commented=first.commented or later.commented,
        reacted=first.reacted or later.reacted,
        reaction_types=tuple(
            dict.fromkeys((*first.reaction_types, *later.reaction_types))
        ),
        interaction_count=first.interaction_count + later.interaction_count,
    )


def _profile_field_value(details: ProfileDetails, field: ProfileField):
    return {
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
    }[field]


def _default_field_status() -> tuple[tuple[str, str], ...]:
    return tuple(
        (field.value, FieldStatus.NOT_REQUESTED.value) for field in ProfileField
    )


def _details_status(
    details: ProfileDetails,
    fields: tuple[ProfileField, ...],
) -> tuple[tuple[str, str], ...]:
    if details.field_status:
        return details.field_status

    requested = frozenset(fields) if fields else frozenset(ProfileField)
    return tuple(
        (
            field.value,
            (
                FieldStatus.NOT_REQUESTED.value
                if field not in requested
                else FieldStatus.FOUND.value
                if _profile_field_value(details, field)
                else FieldStatus.NOT_VISIBLE.value
            ),
        )
        for field in ProfileField
    )


def _details_sources(
    details: ProfileDetails,
    fields: tuple[ProfileField, ...],
) -> tuple[tuple[str, str], ...]:
    if details.field_sources:
        return details.field_sources

    requested = frozenset(fields) if fields else frozenset(ProfileField)
    return tuple(
        (
            field.value,
            (
                ";".join(details.phone_sources)
                if field is ProfileField.PHONE and details.phone_sources
                else "facebook:profile_visible"
            ),
        )
        for field in ProfileField
        if field in requested and _profile_field_value(details, field)
    )


def _seen_record(record: UserRecord, captured_at: str) -> UserRecord:
    return replace(
        record,
        field_status=record.field_status or _default_field_status(),
        first_seen=record.first_seen or captured_at,
        last_seen=captured_at,
    )


def _failed_enrichment_record(
    record: UserRecord,
    fields: tuple[ProfileField, ...],
    status: FieldStatus,
    captured_at: str,
) -> UserRecord:
    requested = frozenset(fields) if fields else frozenset(ProfileField)
    statuses = tuple(
        (
            field.value,
            status.value
            if field in requested
            else FieldStatus.NOT_REQUESTED.value,
        )
        for field in ProfileField
    )
    return replace(
        record,
        field_status=statuses,
        last_enriched_at=captured_at,
    )


def _merge_details(
    record: UserRecord,
    details: ProfileDetails,
    fields: tuple[ProfileField, ...],
    captured_at: str,
) -> UserRecord:
    return replace(
        record,
        name=record.name or details.name,
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
        bio=record.bio or details.bio,
        workplace=record.workplace or details.workplace,
        education=record.education or details.education,
        gender=record.gender or details.gender,
        languages=tuple(
            dict.fromkeys((*record.languages, *details.languages))
        ),
        relationship_status=(
            record.relationship_status or details.relationship_status
        ),
        field_status=_details_status(details, fields),
        field_sources=tuple(
            {
                **dict(record.field_sources),
                **dict(_details_sources(details, fields)),
            }.items()
        ),
        last_enriched_at=captured_at,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuthenticatedService:
    def __init__(
        self,
        session: SessionPort,
        members: CollectionPort,
        comments: CollectionPort,
        parser: UserParserPort,
        profile_enricher: ProfileEnricherPort | None = None,
        *,
        relationships: CollectionPort | None = None,
        reactions: CollectionPort | None = None,
        relationship_parser: UserParserPort | None = None,
        reaction_parser: UserParserPort | None = None,
        messages: CollectionPort | None = None,
        message_parser: MessageParserPort | None = None,
        inspector: InspectorPort | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session
        self._members = members
        self._comments = comments
        self._parser = parser
        self._profile_enricher = profile_enricher
        self._relationships = relationships
        self._reactions = reactions
        self._relationship_parser = relationship_parser
        self._reaction_parser = reaction_parser or relationship_parser
        self._messages = messages
        self._message_parser = message_parser
        self._inspector = inspector
        self._sleep = sleep_func

    def validate(
        self,
        request: ScrapeRequest,
    ) -> None:
        _prepared_targets(request)

        action = AuthenticatedAction(request.action)
        needs_enrichment = (
            request.enrich_profiles or action is AuthenticatedAction.PROFILE
        )

        if needs_enrichment and self._profile_enricher is None:
            raise ValidationError("Profile enrichment runtime is unavailable.")

        if action in {
            AuthenticatedAction.FRIENDS,
            AuthenticatedAction.FOLLOWERS,
        } and (self._relationships is None or self._relationship_parser is None):
            raise ValidationError("Relationship collection runtime is unavailable.")

        if action in {
            AuthenticatedAction.REACTIONS,
            AuthenticatedAction.ENGAGEMENT,
        } and (
            self._reactions is None or self._reaction_parser is None
        ):
            raise ValidationError("Reactions collection runtime is unavailable.")

        if action is AuthenticatedAction.MESSAGES and (
            self._messages is None or self._message_parser is None
        ):
            raise ValidationError("Messages collection runtime is unavailable.")

        if action is AuthenticatedAction.INSPECT and self._inspector is None:
            raise ValidationError("Inspection runtime is unavailable.")

    def run(
        self,
        request: ScrapeRequest,
        browser,
    ) -> (
        ScrapeResult[UserRecord]
        | ScrapeResult[MessageRecord]
        | ScrapeResult[InspectRecord]
        | AuthenticatedBatchResult
    ):
        prepared, issues = _prepared_targets(request)

        action_requested = AuthenticatedAction(request.action)
        needs_enrichment = (
            request.enrich_profiles
            or action_requested is AuthenticatedAction.PROFILE
        )

        if needs_enrichment and self._profile_enricher is None:
            raise ValidationError("Profile enrichment runtime is unavailable.")

        if action_requested is AuthenticatedAction.BATCH:
            return self._run_batch(request, browser, prepared, issues)

        if action_requested is AuthenticatedAction.INSPECT:
            if self._inspector is None:
                raise ValidationError("Inspection runtime is unavailable.")
            return self._run_inspect(request, browser, prepared, issues)

        if action_requested is AuthenticatedAction.MESSAGES:
            if self._messages is None or self._message_parser is None:
                raise ValidationError("Messages collection runtime is unavailable.")
            return self._run_messages(request, browser, prepared, issues)

        if prepared:
            self._session.ensure_authenticated(browser)

        records_by_id: dict[
            str,
            UserRecord,
        ] = {}
        discovered = 0

        for action, url in prepared:
            self._session.assert_authenticated(browser)

            if action is AuthenticatedAction.PROFILE:
                identity = profile_identity_url(url)
                if identity is None:
                    continue
                user_id, profile_url = identity
                records_by_id[user_id] = UserRecord(
                    user_id=user_id,
                    name=None,
                    profile_url=profile_url,
                    source=action.value,
                    source_url=url,
                    first_seen=_utc_now(),
                    last_seen=_utc_now(),
                    field_status=_default_field_status(),
                )
                discovered += 1
                continue

            if action is AuthenticatedAction.ENGAGEMENT:
                jobs = (
                    (
                        AuthenticatedAction.COMMENTS,
                        self._comments,
                        self._parser,
                    ),
                    (
                        AuthenticatedAction.REACTIONS,
                        self._reactions,
                        self._reaction_parser,
                    ),
                )
            elif action is AuthenticatedAction.MEMBERS:
                jobs = ((action, self._members, self._parser),)
            elif action is AuthenticatedAction.COMMENTS:
                jobs = ((action, self._comments, self._parser),)
            elif action in {
                AuthenticatedAction.FRIENDS,
                AuthenticatedAction.FOLLOWERS,
            }:
                jobs = ((action, self._relationships, self._relationship_parser),)
            else:
                jobs = ((action, self._reactions, self._reaction_parser),)

            for collection_action, collector, parser in jobs:
                if collector is None or parser is None:
                    raise ValidationError(
                        f"{collection_action.value.capitalize()} collection "
                        "runtime is unavailable."
                    )

                try:
                    html, _ = collector.collect(
                        browser,
                        url,
                        steps=request.steps,
                        delay_seconds=(request.delay_seconds),
                    )

                    try:
                        parsed = parser.parse(
                            html,
                            source=action.value,
                            source_url=url,
                        )

                    except BrowserParseError:
                        raise

                    except Exception as error:
                        raise BrowserParseError(
                            "Authenticated user parsing failed.",
                            target=url,
                        ) from error

                    discovered += len(parsed)
                    captured_at = _utc_now()
                    for record in parsed:
                        if collection_action is AuthenticatedAction.COMMENTS:
                            record = replace(
                                record,
                                commented=True,
                                interaction_count=max(
                                    1, record.interaction_count
                                ),
                            )
                        elif collection_action is AuthenticatedAction.REACTIONS:
                            record = replace(
                                record,
                                reacted=True,
                                interaction_count=max(
                                    1, record.interaction_count
                                ),
                            )

                        record = _seen_record(record, captured_at)
                        if action in {
                            AuthenticatedAction.FRIENDS,
                            AuthenticatedAction.FOLLOWERS,
                        }:
                            owner = profile_identity_url(url)
                            if owner is not None and (
                                record.user_id.casefold() == owner[0].casefold()
                            ):
                                continue

                        existing = records_by_id.get(record.user_id)

                        records_by_id[record.user_id] = (
                            record
                            if existing is None
                            else _merge_record(existing, record)
                        )

                except SessionError:
                    raise

                except (
                    BrowserNavigationError,
                    BrowserParseError,
                ) as error:
                    issue_action = (
                        f"engagement_{collection_action.value}"
                        if action is AuthenticatedAction.ENGAGEMENT
                        else action.value
                    )
                    issues.append(
                        ScrapeIssue(
                            code=error.code,
                            message=error.safe_message,
                            target=(error.target or url),
                            mode=(ScrapeMode.AUTHENTICATED),
                            action=issue_action,
                        )
                    )

        enrichment: EnrichmentStats | None = None

        if needs_enrichment:
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
                    failure_status = (
                        FieldStatus.NAVIGATION_FAILED
                        if isinstance(error, BrowserNavigationError)
                        else FieldStatus.SECTION_UNAVAILABLE
                    )
                    records_by_id[user_id] = _failed_enrichment_record(
                        record,
                        fields,
                        failure_status,
                        _utc_now(),
                    )
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
                    records_by_id[user_id] = _failed_enrichment_record(
                        record,
                        fields,
                        FieldStatus.SECTION_UNAVAILABLE,
                        _utc_now(),
                    )
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
                    records_by_id[user_id] = _merge_details(
                        record,
                        details,
                        fields,
                        _utc_now(),
                    )
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

    def _run_batch(
        self,
        request: ScrapeRequest,
        browser,
        prepared: list[PreparedTarget],
        initial_issues: list[ScrapeIssue],
    ) -> AuthenticatedBatchResult:
        grouped: dict[AuthenticatedAction, list[str]] = {}
        for action, target in prepared:
            grouped.setdefault(action, []).append(target)

        user_records: dict[str, UserRecord] = {}
        message_records: dict[str, MessageRecord] = {}
        inspect_records: dict[str, InspectRecord] = {}
        user_issues = list(initial_issues)
        message_issues: list[ScrapeIssue] = []
        inspect_issues: list[ScrapeIssue] = []
        discovered = 0
        enrichment_values: list[EnrichmentStats] = []

        for action, targets in grouped.items():
            message_action = action is AuthenticatedAction.MESSAGES
            inspect_action = action is AuthenticatedAction.INSPECT
            subrequest = replace(
                request,
                action=action,
                targets=tuple(targets),
                enrich_profiles=(
                    False
                    if message_action or inspect_action
                    else request.enrich_profiles
                ),
                profile_fields=(
                    ()
                    if message_action or inspect_action
                    else request.profile_fields
                ),
                resume=False,
                incremental=False,
                checkpoint_path=None,
            )
            result = self.run(subrequest, browser)
            discovered += result.stats.discovered

            if inspect_action:
                for record in result.records:
                    inspect_records[record.target_url] = record
                inspect_issues.extend(result.issues)
            elif message_action:
                for record in result.records:
                    existing = message_records.get(record.message_id)
                    message_records[record.message_id] = (
                        record
                        if existing is None
                        else replace(
                            existing,
                            sender_name=(
                                existing.sender_name or record.sender_name
                            ),
                            sender_profile_url=(
                                existing.sender_profile_url
                                or record.sender_profile_url
                            ),
                            first_seen=(
                                existing.first_seen or record.first_seen
                            ),
                            last_seen=(record.last_seen or existing.last_seen),
                        )
                    )
                message_issues.extend(result.issues)
            else:
                for record in result.records:
                    existing = user_records.get(record.user_id)
                    user_records[record.user_id] = (
                        record
                        if existing is None
                        else _merge_record(existing, record)
                    )
                user_issues.extend(result.issues)
                if result.enrichment is not None:
                    enrichment_values.append(result.enrichment)

        enrichment = (
            EnrichmentStats(
                selected=sum(item.selected for item in enrichment_values),
                attempted=sum(item.attempted for item in enrichment_values),
                succeeded=sum(item.succeeded for item in enrichment_values),
                failed=sum(item.failed for item in enrichment_values),
                phone_found=sum(item.phone_found for item in enrichment_values),
                address_found=sum(
                    item.address_found for item in enrichment_values
                ),
                current_city_found=sum(
                    item.current_city_found for item in enrichment_values
                ),
                hometown_found=sum(
                    item.hometown_found for item in enrichment_values
                ),
                birth_year_found=sum(
                    item.birth_year_found for item in enrichment_values
                ),
            )
            if enrichment_values
            else None
        )
        all_issues = (*user_issues, *message_issues, *inspect_issues)
        user_result = ScrapeResult(
            records=tuple(user_records.values()),
            issues=tuple(user_issues),
            stats=ScrapeStats(
                requested=sum(
                    len(targets)
                    for action, targets in grouped.items()
                    if action is not AuthenticatedAction.MESSAGES
                    and action is not AuthenticatedAction.INSPECT
                ),
                discovered=discovered,
                succeeded=len(user_records),
                failed=len(user_issues),
            ),
            enrichment=enrichment,
        )
        message_result = ScrapeResult(
            records=tuple(message_records.values()),
            issues=tuple(message_issues),
            stats=ScrapeStats(
                requested=len(grouped.get(AuthenticatedAction.MESSAGES, ())),
                discovered=len(message_records),
                succeeded=len(message_records),
                failed=len(message_issues),
            ),
        )
        inspect_result = ScrapeResult(
            records=tuple(inspect_records.values()),
            issues=tuple(inspect_issues),
            stats=ScrapeStats(
                requested=len(grouped.get(AuthenticatedAction.INSPECT, ())),
                discovered=len(inspect_records),
                succeeded=len(inspect_records),
                failed=len(inspect_issues),
            ),
        )
        return AuthenticatedBatchResult(
            user_result=user_result,
            message_result=message_result,
            inspect_result=inspect_result,
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=discovered,
                succeeded=(
                    len(user_records)
                    + len(message_records)
                    + len(inspect_records)
                ),
                failed=len(all_issues),
            ),
            issues=tuple(all_issues),
            enrichment=enrichment,
        )

    def _run_inspect(
        self,
        request: ScrapeRequest,
        browser,
        prepared: list[PreparedTarget],
        issues: list[ScrapeIssue],
    ) -> ScrapeResult[InspectRecord]:
        if prepared:
            self._session.ensure_authenticated(browser)

        records: dict[str, InspectRecord] = {}

        for action, url in prepared:
            self._session.assert_authenticated(browser)
            try:
                record = self._inspector.inspect(browser, url)
                records[url] = record
            except SessionError:
                raise
            except BrowserNavigationError as error:
                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=error.target or url,
                        mode=ScrapeMode.AUTHENTICATED,
                        action=action.value,
                    )
                )

        return ScrapeResult(
            records=tuple(records.values()),
            issues=tuple(issues),
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=len(records),
                succeeded=len(records),
                failed=len(issues),
            ),
        )

    def _run_messages(
        self,
        request: ScrapeRequest,
        browser,
        prepared: list[PreparedTarget],
        issues: list[ScrapeIssue],
    ) -> ScrapeResult[MessageRecord]:
        if prepared:
            self._session.ensure_authenticated(browser)

        records_by_id: dict[str, MessageRecord] = {}
        discovered = 0

        for action, url in prepared:
            self._session.assert_authenticated(browser)

            try:
                html, _ = self._messages.collect(
                    browser,
                    url,
                    steps=request.steps,
                    delay_seconds=request.delay_seconds,
                )

                try:
                    parsed = self._message_parser.parse(html, source_url=url)
                except BrowserParseError:
                    raise
                except Exception as error:
                    raise BrowserParseError(
                        "Authenticated message parsing failed.", target=url
                    ) from error

                discovered += len(parsed)
                captured_at = _utc_now()
                for record in parsed:
                    record = replace(
                        record,
                        first_seen=record.first_seen or captured_at,
                        last_seen=captured_at,
                    )
                    records_by_id.setdefault(record.message_id, record)

            except SessionError:
                raise
            except (BrowserNavigationError, BrowserParseError) as error:
                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=error.target or url,
                        mode=ScrapeMode.AUTHENTICATED,
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
