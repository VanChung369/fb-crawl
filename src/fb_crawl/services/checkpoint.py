from __future__ import annotations

import json
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
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.exporters.atomic import atomic_text_writer
from fb_crawl.services.authenticated import _merge_record, _prepared_targets


CHECKPOINT_SCHEMA_VERSION = 1


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
    return UserRecord(
        **_tuple_values(
            data,
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
        )

    if action is AuthenticatedAction.MESSAGES:
        return _empty_result(), result, _empty_result(), None

    if action is AuthenticatedAction.INSPECT:
        return _empty_result(), _empty_result(), result, None

    return result, _empty_result(), _empty_result(), result.enrichment


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
    def __init__(self, service: AuthenticatedServicePort) -> None:
        self._service = service

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

        return payload

    def validate(self, request: ScrapeRequest) -> None:
        self._service.validate(request)
        if request.resume or request.incremental:
            self._validated_state(request)

    def run(self, request: ScrapeRequest, browser):
        if not (request.resume or request.incremental):
            return self._service.run(request, browser)

        store = self._store(request)
        state = self._validated_state(request)
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

        for raw_target in request.targets:
            single = replace(
                request,
                targets=(raw_target,),
                resume=False,
                incremental=False,
                checkpoint_path=None,
            )
            key = _single_target_key(single)

            if request.resume and key in completed:
                continue

            normalized_target = key.split(":", 1)[1] if ":" in key else key
            output_issues = [
                issue
                for issue in output_issues
                if issue.target != normalized_target
            ]
            result = self._service.run(single, browser)
            action = AuthenticatedAction(single.action)
            users, messages, inspections, enrichment = _parts(result, action)

            for record in users.records:
                was_known = record.user_id in known_users
                existing = known_users.get(record.user_id)
                known_users[record.user_id] = (
                    record if existing is None else _merge_record(existing, record)
                )
                if request.resume or not was_known:
                    output_users[record.user_id] = known_users[record.user_id]

            for record in messages.records:
                was_known = record.message_id in known_messages
                existing = known_messages.get(record.message_id)
                known_messages[record.message_id] = (
                    record if existing is None else _merge_message(existing, record)
                )
                if request.resume or not was_known:
                    output_messages[record.message_id] = known_messages[
                        record.message_id
                    ]

            for record in inspections.records:
                was_known = record.target_url in known_inspect
                known_inspect[record.target_url] = record
                if request.resume or not was_known:
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

            store.save(
                {
                    "schema_version": CHECKPOINT_SCHEMA_VERSION,
                    "action": str(request.action),
                    "target_keys": list(_target_keys(request)),
                    "completed_targets": sorted(completed),
                    "users": [asdict(item) for item in known_users.values()],
                    "messages": [
                        asdict(item) for item in known_messages.values()
                    ],
                    "inspect": [asdict(item) for item in known_inspect.values()],
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
        user_result = ScrapeResult(
            records=tuple(output_users.values()),
            issues=tuple(
                issue for issue in output_issues if issue.action != "messages"
            ),
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=len(output_users),
                succeeded=len(output_users),
                failed=len(output_issues),
            ),
            enrichment=enrichment,
        )
        message_result = ScrapeResult(
            records=tuple(output_messages.values()),
            issues=tuple(
                issue for issue in output_issues if issue.action == "messages"
            ),
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=len(output_messages),
                succeeded=len(output_messages),
                failed=len(output_issues),
            ),
        )
        inspect_result = ScrapeResult(
            records=tuple(output_inspect.values()),
            issues=(),
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=len(output_inspect),
                succeeded=len(output_inspect),
                failed=0,
            ),
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
        )
