from dataclasses import replace
from pathlib import Path

import pytest

from fb_crawl.core.exceptions import SessionError, ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeMode,
    ScrapeIssue,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.services.checkpoint import CheckpointingService


def record(user_id: str, source_url: str) -> UserRecord:
    return UserRecord(
        user_id=user_id,
        name=f"User {user_id}",
        profile_url=f"https://www.facebook.com/profile.php?id={user_id}",
        source="members",
        source_url=source_url,
    )


def result(*records: UserRecord) -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=records,
        issues=(),
        stats=ScrapeStats(
            requested=1,
            discovered=len(records),
            succeeded=len(records),
            failed=0,
        ),
    )


def request(path: Path, *targets: str, incremental: bool = False) -> ScrapeRequest:
    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.MEMBERS,
        targets=targets,
        resume=not incremental,
        incremental=incremental,
        checkpoint_path=str(path),
    )


class PerTargetService:
    def __init__(self, *, fail_target: str | None = None) -> None:
        self.fail_target = fail_target
        self.calls: list[str] = []

    def validate(self, request: ScrapeRequest) -> None:
        return None

    def run(self, request: ScrapeRequest, browser):
        target = request.targets[0]
        self.calls.append(target)
        if target == self.fail_target:
            raise SessionError("Session unavailable.")
        user_id = target.rstrip("/").split("/")[-1]
        return result(record(user_id, target))


def test_resume_preserves_completed_targets_after_session_loss(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    targets = (
        "https://www.facebook.com/groups/100",
        "https://www.facebook.com/groups/200",
    )
    failing = PerTargetService(fail_target=targets[1])
    service = CheckpointingService(failing)

    with pytest.raises(SessionError):
        service.run(request(checkpoint, *targets), object())

    assert checkpoint.exists()
    assert failing.calls == list(targets)

    resumed_base = PerTargetService()
    resumed = CheckpointingService(resumed_base).run(
        request(checkpoint, *targets), object()
    )

    assert resumed_base.calls == [targets[1]]
    assert [item.user_id for item in resumed.records] == ["100", "200"]


class IncrementalService:
    def __init__(self, records: tuple[UserRecord, ...]) -> None:
        self.records = records

    def validate(self, request: ScrapeRequest) -> None:
        return None

    def run(self, request: ScrapeRequest, browser):
        return result(*self.records)


def test_incremental_emits_only_new_identities(tmp_path: Path) -> None:
    checkpoint = tmp_path / "incremental.json"
    target = "https://www.facebook.com/groups/100"
    first = record("100", target)
    second = record("200", target)

    seeded = CheckpointingService(IncrementalService((first,))).run(
        request(checkpoint, target, incremental=True), object()
    )
    assert [item.user_id for item in seeded.records] == ["100"]

    incremental = CheckpointingService(
        IncrementalService((first, second))
    ).run(request(checkpoint, target, incremental=True), object())

    assert [item.user_id for item in incremental.records] == ["200"]


def test_checkpoint_target_mismatch_fails_validation(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    first_target = "https://www.facebook.com/groups/100"
    CheckpointingService(PerTargetService()).run(
        request(checkpoint, first_target), object()
    )

    service = CheckpointingService(PerTargetService())
    with pytest.raises(ValidationError, match="targets do not match"):
        service.validate(
            request(checkpoint, "https://www.facebook.com/groups/200")
        )


def test_checkpoint_depth_or_time_mismatch_fails_validation(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    target = "https://www.facebook.com/groups/100"
    original = replace(
        request(checkpoint, target),
        depth=1,
        max_duration_seconds=60,
    )
    CheckpointingService(PerTargetService()).run(original, object())

    changed = replace(original, depth=2)

    with pytest.raises(ValidationError, match="options do not match"):
        CheckpointingService(PerTargetService()).validate(changed)


def issue_result(
    target: str,
    *,
    code: str = "authenticated_navigation_failed",
    retryable: bool = True,
) -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=(),
        issues=(
            ScrapeIssue(
                code=code,
                message="Authenticated target failed.",
                target=target,
                mode=ScrapeMode.AUTHENTICATED,
                action=AuthenticatedAction.MEMBERS.value,
                retryable=retryable,
            ),
        ),
        stats=ScrapeStats(
            requested=1,
            discovered=0,
            succeeded=0,
            failed=1,
        ),
    )


class OutcomeService:
    def __init__(self, outcomes: dict[str, list[object]]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def validate(self, request: ScrapeRequest) -> None:
        return None

    def run(self, request: ScrapeRequest, browser):
        target = request.targets[0]
        self.calls.append(target)
        outcome = self.outcomes[target].pop(0)

        if isinstance(outcome, BaseException):
            raise outcome

        return outcome


def plain_request(*targets: str, **changes) -> ScrapeRequest:
    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.MEMBERS,
        targets=targets,
        **changes,
    )


def test_retryable_target_uses_backoff_then_keeps_success() -> None:
    target = "https://www.facebook.com/groups/100"
    service = OutcomeService(
        {
            target: [
                issue_result(target),
                result(record("100", target)),
            ]
        }
    )
    sleeps = []
    outcome = CheckpointingService(
        service,
        sleep_func=sleeps.append,
        jitter_func=lambda low, high: high / 2,
    ).run(
        plain_request(
            target,
            max_retries=1,
            retry_backoff_seconds=2,
            retry_jitter_seconds=0.5,
        ),
        object(),
    )

    assert service.calls == [target, target]
    assert [item.user_id for item in outcome.records] == ["100"]
    assert outcome.issues == ()
    assert outcome.retry.retried == 1
    assert outcome.retry.pending == 0
    assert sleeps == [2.25]


def test_nonretryable_target_is_not_repeated() -> None:
    target = "https://www.facebook.com/groups/100"
    service = OutcomeService(
        {target: [issue_result(target, retryable=False)]}
    )

    outcome = CheckpointingService(service).run(
        plain_request(target, max_retries=3),
        object(),
    )

    assert service.calls == [target]
    assert outcome.retry.retried == 0
    assert outcome.retry.pending == 0


def test_rate_limit_issue_is_counted_across_attempts() -> None:
    target = "https://www.facebook.com/groups/100"
    service = OutcomeService(
        {
            target: [
                issue_result(target, code="authenticated_rate_limited"),
                result(record("100", target)),
            ]
        }
    )

    outcome = CheckpointingService(
        service,
        sleep_func=lambda seconds: None,
        jitter_func=lambda low, high: 0,
    ).run(plain_request(target, max_retries=1), object())

    assert outcome.retry.rate_limited == 1
    assert outcome.retry.retried == 1


def test_keyboard_interrupt_returns_completed_targets_and_pending_count() -> None:
    first = "https://www.facebook.com/groups/100"
    second = "https://www.facebook.com/groups/200"
    service = OutcomeService(
        {
            first: [result(record("100", first))],
            second: [KeyboardInterrupt()],
        }
    )

    outcome = CheckpointingService(service).run(
        plain_request(first, second),
        object(),
    )

    assert [item.user_id for item in outcome.records] == ["100"]
    assert outcome.retry.attempted_targets == 2
    assert outcome.retry.interrupted == 1
    assert outcome.retry.pending == 1
    assert outcome.issues[-1].code == "authenticated_interrupted"


def test_resume_checkpoint_skips_target_completed_before_interrupt(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "interrupt.json"
    first = "https://www.facebook.com/groups/100"
    second = "https://www.facebook.com/groups/200"
    interrupted_service = OutcomeService(
        {
            first: [result(record("100", first))],
            second: [KeyboardInterrupt()],
        }
    )
    interrupted_request = request(checkpoint, first, second)

    partial = CheckpointingService(interrupted_service).run(
        interrupted_request,
        object(),
    )
    assert partial.retry.interrupted == 1

    resumed_service = OutcomeService(
        {second: [result(record("200", second))]}
    )
    resumed = CheckpointingService(resumed_service).run(
        interrupted_request,
        object(),
    )

    assert resumed_service.calls == [second]
    assert [item.user_id for item in resumed.records] == ["100", "200"]
    assert resumed.retry.pending == 0


def test_interrupted_inspect_keeps_issue_for_json_export() -> None:
    target = "https://www.facebook.com/synthetic.user"
    service = OutcomeService({target: [KeyboardInterrupt()]})
    inspect_request = ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.INSPECT,
        targets=(target,),
    )

    outcome = CheckpointingService(service).run(inspect_request, object())

    assert outcome.retry.interrupted == 1
    assert outcome.stats.failed == 1
    assert outcome.issues[0].code == "authenticated_interrupted"
