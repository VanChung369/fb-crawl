from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Protocol

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    AuthenticatedBatchResult,
    EnrichmentStats,
    InspectRecord,
    MessageRecord,
    PhoneEvidence,
    RetryStats,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    UidResolutionStats,
    UserRecord,
)
from fb_crawl.exporters.atomic import atomic_text_writer
from fb_crawl.services.authenticated import _merge_record, _prepared_targets


CHECKPOINT_SCHEMA_VERSION = 1
RATE_LIMIT_CODE = "authenticated_rate_limited"


class AuthenticatedServicePort(Protocol):
    def validate(self, request: ScrapeRequest) -> None: ...

    def run(self, request: ScrapeRequest, browser): ...


def _empty_result() -> ScrapeResult:
    return ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(requested=0, discovered=0, succeeded=0, failed=0),
    )


def _tuple_values(data: dict, *keys: str) -> dict:
    normalized = dict(data)
    for key in keys:
        if key in normalized:
            normalized[key] = tuple(normalized[key] or ())
    return normalized


def _user_from_json(data: dict) -> UserRecord:
    normalized = dict(data)
    normalized["phone_evidence"] = tuple(
        PhoneEvidence(**item)
        for item in normalized.get("phone_evidence", ())
    )
    return UserRecord(
        **_tuple_values(
            normalized,
            "phone_numbers",
            "phone_sources",
            "languages",
            "field_status",
            "field_sources",
            "reaction_types",
        )
    )


def _message_from_json(data: dict) -> MessageRecord:
    return MessageRecord(**data)


def _inspect_from_json(data: dict) -> InspectRecord:
    return InspectRecord(**data)


def _issue_from_json(data: dict) -> ScrapeIssue:
    normalized = dict(data)
    normalized["mode"] = ScrapeMode(normalized["mode"])
    return ScrapeIssue(**normalized)


def _merge_message(first: MessageRecord, later: MessageRecord) -> MessageRecord:
    return replace(
        first,
        sender_name=first.sender_name or later.sender_name,
        sender_profile_url=(
            first.sender_profile_url or later.sender_profile_url
        ),
        text=later.text or first.text,
        sent_at=later.sent_at or first.sent_at,
        first_seen=first.first_seen or later.first_seen,
        last_seen=later.last_seen or first.last_seen,
    )


def _parts(result, action: AuthenticatedAction):
    if isinstance(result, AuthenticatedBatchResult):
        return (
            result.user_result,
            result.message_result,
            result.inspect_result,
            result.enrichment,
            result.uid_resolution,
        )

    if action is AuthenticatedAction.MESSAGES:
        return _empty_result(), result, _empty_result(), None, None

    if action is AuthenticatedAction.INSPECT:
        return _empty_result(), _empty_result(), result, None, None

    return (
        result,
        _empty_result(),
        _empty_result(),
        result.enrichment,
        result.uid_resolution,
    )


def _result_issues(
    result,
    action: AuthenticatedAction,
) -> tuple[ScrapeIssue, ...]:
    users, messages, inspections, _, _ = _parts(result, action)
    return (*users.issues, *messages.issues, *inspections.issues)


def _result_quality(result, action: AuthenticatedAction) -> tuple[int, int]:
    users, messages, inspections, _, _ = _parts(result, action)
    issues = (*users.issues, *messages.issues, *inspections.issues)
    records = (
        len(users.records)
        + len(messages.records)
        + len(inspections.records)
    )
    return len(issues), -records


def _target_keys(request: ScrapeRequest) -> tuple[str, ...]:
    keys: list[str] = []

    for raw in request.targets:
        single = replace(request, targets=(raw,))
        prepared, _ = _prepared_targets(single)

        if prepared:
            keys.extend(f"{action.value}:{url}" for action, url in prepared)
        else:
            safe = raw.split("?", 1)[0].strip()
            keys.append(f"invalid:{safe}")

    return tuple(sorted(dict.fromkeys(keys)))


def _single_target_key(request: ScrapeRequest) -> str:
    return _target_keys(request)[0]


def _request_options(request: ScrapeRequest) -> dict:
    return {
        "steps": request.steps,
        "max_duration_seconds": request.max_duration_seconds,
        "depth": request.depth,
        "max_nodes": request.max_nodes,
        "delay_seconds": request.delay_seconds,
        "enrich_profiles": request.enrich_profiles,
        "profile_fields": [field.value for field in request.profile_fields],
        "profile_limit": request.profile_limit,
        "profile_delay_seconds": request.profile_delay_seconds,
        "phone_post_steps": request.phone_post_steps,
        "phone_post_duration_seconds": (
            request.phone_post_duration_seconds
        ),
        "force_uid_refresh": request.force_uid_refresh,
    }


class JsonCheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict | None:
        if not self.path.exists():
            return None

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValidationError(
                f"Cannot read checkpoint {self.path}."
            ) from error

        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValidationError("Unsupported checkpoint format.")

        return payload

    def save(self, payload: dict) -> None:
        with atomic_text_writer(self.path, encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")


class CheckpointingService:
    def __init__(
        self,
        service: AuthenticatedServicePort,
        *,
        sleep_func: Callable[[float], None] = time.sleep,
        jitter_func: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._service = service
        self._sleep = sleep_func
        self._jitter = jitter_func

    def _run_with_retry(self, request: ScrapeRequest, browser):
        action = AuthenticatedAction(request.action)
        best = None
        retried = 0
        rate_limited = 0

        for retry_index in range(request.max_retries + 1):
            result = self._service.run(request, browser)
            issues = _result_issues(result, action)
            rate_limited += sum(
                issue.code == RATE_LIMIT_CODE for issue in issues
            )

            if best is None or _result_quality(
                result,
                action,
            ) <= _result_quality(best, action):
                best = result

            retryable = any(issue.retryable for issue in issues)

            if not retryable or retry_index >= request.max_retries:
                return best, retried, rate_limited, retryable

            retried += 1
            backoff = min(
                request.retry_backoff_seconds * (2**retry_index),
                300.0,
            )
            self._sleep(
                backoff
                + self._jitter(0.0, request.retry_jitter_seconds)
            )

        raise AssertionError("bounded authenticated retry loop exhausted")

    def _store(self, request: ScrapeRequest) -> JsonCheckpointStore:
        if not request.checkpoint_path:
            raise ValidationError(
                "Resume and incremental modes require a checkpoint path."
            )
        return JsonCheckpointStore(Path(request.checkpoint_path))

    def _validated_state(self, request: ScrapeRequest) -> dict | None:
        payload = self._store(request).load()
        if payload is None:
            return None

        if payload.get("action") != str(request.action):
            raise ValidationError("Checkpoint action does not match this run.")

        if tuple(payload.get("target_keys") or ()) != _target_keys(request):
            raise ValidationError("Checkpoint targets do not match this run.")

        if payload.get("request_options") != _request_options(request):
            raise ValidationError("Checkpoint options do not match this run.")

        return payload

    def validate(self, request: ScrapeRequest) -> None:
        self._service.validate(request)
        if request.resume or request.incremental:
            self._validated_state(request)

    def run(self, request: ScrapeRequest, browser):
        checkpoint_enabled = request.resume or request.incremental
        store = self._store(request) if checkpoint_enabled else None
        state = self._validated_state(request) if checkpoint_enabled else None
        known_users = {
            item["user_id"]: _user_from_json(item)
            for item in (state or {}).get("users", ())
        }
        known_messages = {
            item["message_id"]: _message_from_json(item)
            for item in (state or {}).get("messages", ())
        }
        known_inspect = {
            item["target_url"]: _inspect_from_json(item)
            for item in (state or {}).get("inspect", ())
        }
        completed = set((state or {}).get("completed_targets", ()))
        stored_issues = [
            _issue_from_json(item) for item in (state or {}).get("issues", ())
        ]

        output_users = dict(known_users) if request.resume else {}
        output_messages = dict(known_messages) if request.resume else {}
        output_inspect = dict(known_inspect) if request.resume else {}
        output_issues = list(stored_issues) if request.resume else []
        enrichment_values: list[EnrichmentStats] = []
        uid_values: list[UidResolutionStats] = []
        attempted_targets = 0
        retried = 0
        rate_limited = 0
        pending = 0
        interrupted = 0
        pending_targets = []

        for raw_target in request.targets:
            candidate = replace(
                request,
                targets=(raw_target,),
                resume=False,
                incremental=False,
                checkpoint_path=None,
            )
            key = _single_target_key(candidate)

            if request.resume and key in completed:
                continue

            pending_targets.append(raw_target)

        for target_index, raw_target in enumerate(pending_targets):
            single = replace(
                request,
                targets=(raw_target,),
                resume=False,
                incremental=False,
                checkpoint_path=None,
            )
            key = _single_target_key(single)

            normalized_target = key.split(":", 1)[1] if ":" in key else key
            output_issues = [
                issue
                for issue in output_issues
                if issue.target != normalized_target
            ]
            attempted_targets += 1

            try:
                (
                    result,
                    target_retried,
                    target_rate_limited,
                    retryable_failure,
                ) = self._run_with_retry(single, browser)
            except KeyboardInterrupt:
                interrupted = 1
                pending += len(pending_targets) - target_index
                issue = ScrapeIssue(
                    code="authenticated_interrupted",
                    message="Authenticated collection was interrupted safely.",
                    target=normalized_target,
                    mode=ScrapeMode.AUTHENTICATED,
                    action=str(request.action),
                    retryable=True,
                )
                output_issues.append(issue)
                stored_issues = [
                    item
                    for item in stored_issues
                    if item.target != normalized_target
                ]
                stored_issues.append(issue)

                if store is not None:
                    store.save(
                        {
                            "schema_version": CHECKPOINT_SCHEMA_VERSION,
                            "action": str(request.action),
                            "target_keys": list(_target_keys(request)),
                            "request_options": _request_options(request),
                            "completed_targets": sorted(completed),
                            "users": [
                                asdict(item) for item in known_users.values()
                            ],
                            "messages": [
                                asdict(item)
                                for item in known_messages.values()
                            ],
                            "inspect": [
                                asdict(item) for item in known_inspect.values()
                            ],
                            "issues": [
                                asdict(item) for item in stored_issues
                            ],
                        }
                    )

                break

            retried += target_retried
            rate_limited += target_rate_limited
            pending += retryable_failure
            action = AuthenticatedAction(single.action)
            (
                users,
                messages,
                inspections,
                enrichment,
                uid_resolution,
            ) = _parts(result, action)

            for record in users.records:
                bounded_users = action in {
                    AuthenticatedAction.FRIENDS,
                    AuthenticatedAction.FOLLOWERS,
                    AuthenticatedAction.BATCH,
                }

                if (
                    bounded_users
                    and record.user_id not in known_users
                    and len(known_users) >= request.max_nodes
                ):
                    continue

                was_known = record.user_id in known_users
                existing = known_users.get(record.user_id)
                known_users[record.user_id] = (
                    record if existing is None else _merge_record(existing, record)
                )
                if not request.incremental or not was_known:
                    output_users[record.user_id] = known_users[record.user_id]

            for record in messages.records:
                was_known = record.message_id in known_messages
                existing = known_messages.get(record.message_id)
                known_messages[record.message_id] = (
                    record if existing is None else _merge_message(existing, record)
                )
                if not request.incremental or not was_known:
                    output_messages[record.message_id] = known_messages[
                        record.message_id
                    ]

            for record in inspections.records:
                was_known = record.target_url in known_inspect
                known_inspect[record.target_url] = record
                if not request.incremental or not was_known:
                    output_inspect[record.target_url] = record

            current_issues = [*users.issues, *messages.issues, *inspections.issues]
            output_issues.extend(current_issues)
            stored_issues = [
                issue for issue in stored_issues if issue.target != normalized_target
            ]
            stored_issues.extend(current_issues)

            if not current_issues:
                completed.add(key)

            if enrichment is not None:
                enrichment_values.append(enrichment)
            if uid_resolution is not None:
                uid_values.append(uid_resolution)

            if store is not None:
                store.save(
                    {
                        "schema_version": CHECKPOINT_SCHEMA_VERSION,
                        "action": str(request.action),
                        "target_keys": list(_target_keys(request)),
                        "request_options": _request_options(request),
                        "completed_targets": sorted(completed),
                        "users": [
                            asdict(item) for item in known_users.values()
                        ],
                        "messages": [
                            asdict(item) for item in known_messages.values()
                        ],
                        "inspect": [
                            asdict(item) for item in known_inspect.values()
                        ],
                        "issues": [asdict(item) for item in stored_issues],
                    }
                )

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
        uid_resolution = (
            UidResolutionStats(
                selected=sum(item.selected for item in uid_values),
                cached=sum(item.cached for item in uid_values),
                resolved=sum(item.resolved for item in uid_values),
                failed=sum(item.failed for item in uid_values),
            )
            if uid_values
            else None
        )
        retry_stats = RetryStats(
            attempted_targets=attempted_targets,
            retried=retried,
            rate_limited=rate_limited,
            pending=pending,
            interrupted=interrupted,
        )
        message_issues = tuple(
            issue for issue in output_issues if issue.action == "messages"
        )
        inspect_issues = tuple(
            issue for issue in output_issues if issue.action == "inspect"
        )
        user_issues = tuple(
            issue
            for issue in output_issues
            if issue.action not in {"messages", "inspect"}
        )
        user_result = ScrapeResult(
            records=tuple(output_users.values()),
            issues=user_issues,
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=len(output_users),
                succeeded=len(output_users),
                failed=len(user_issues),
            ),
            enrichment=enrichment,
            uid_resolution=uid_resolution,
            retry=retry_stats,
        )
        message_result = ScrapeResult(
            records=tuple(output_messages.values()),
            issues=message_issues,
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=len(output_messages),
                succeeded=len(output_messages),
                failed=len(message_issues),
            ),
            retry=retry_stats,
        )
        inspect_result = ScrapeResult(
            records=tuple(output_inspect.values()),
            issues=inspect_issues,
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=len(output_inspect),
                succeeded=len(output_inspect),
                failed=len(inspect_issues),
            ),
            retry=retry_stats,
        )
        action = AuthenticatedAction(request.action)

        if action is AuthenticatedAction.MESSAGES:
            return message_result
        if action is AuthenticatedAction.INSPECT:
            return inspect_result
        if action is not AuthenticatedAction.BATCH:
            return user_result

        return AuthenticatedBatchResult(
            user_result=user_result,
            message_result=message_result,
            inspect_result=inspect_result,
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=(
                    len(output_users)
                    + len(output_messages)
                    + len(output_inspect)
                ),
                succeeded=(
                    len(output_users)
                    + len(output_messages)
                    + len(output_inspect)
                ),
                failed=len(output_issues),
            ),
            issues=tuple(output_issues),
            enrichment=enrichment,
            uid_resolution=uid_resolution,
            retry=retry_stats,
        )
