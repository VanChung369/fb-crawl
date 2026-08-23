import json
from dataclasses import replace
from pathlib import Path

import pytest

from fb_crawl.core.exceptions import SessionError, ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    PhoneEvidence,
    ScrapeMode,
    ScrapeIssue,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.services.checkpoint import CheckpointingService
from fb_crawl.services.execution_control import CrawlCancelled, JobBudgetReached
from fb_crawl.services.execution_control import AccountSafetyStop
from fb_crawl.adapters.browser.account_safety import SafetyCode, SafetySignal


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


def test_resume_reconstructs_nested_phone_evidence(tmp_path: Path) -> None:
    checkpoint = tmp_path / "phone-evidence.json"
    target = "https://www.facebook.com/groups/100"
    enriched = replace(
        record("100", target),
        phone_evidence=(
            PhoneEvidence(
                value="0912 345 678",
                source="facebook:post_text",
                source_url="https://www.facebook.com/example/posts/1",
                captured_at="2026-08-09T01:02:03+00:00",
            ),
        ),
    )
    CheckpointingService(IncrementalService((enriched,))).run(
        request(checkpoint, target),
        object(),
    )
    resumed_base = PerTargetService()

    resumed = CheckpointingService(resumed_base).run(
        request(checkpoint, target),
        object(),
    )

    assert resumed_base.calls == []
    assert resumed.records[0].phone_evidence == enriched.phone_evidence


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


def test_budget_exhaustion_keeps_checkpoint_target_retryable_and_incomplete(
    tmp_path: Path,
) -> None:
    """Break caught: a bounded partial target is saved as completed and skipped."""
    checkpoint = tmp_path / "budget.json"
    target = "https://www.facebook.com/groups/100"
    partial = ScrapeResult(
        records=(record("100", target),),
        issues=(
            ScrapeIssue(
                "authenticated_budget_exhausted",
                "Authenticated crawl budget was exhausted.",
                target,
                ScrapeMode.AUTHENTICATED,
                "members",
                True,
            ),
        ),
        stats=ScrapeStats(1, 1, 1, 1),
    )
    service = OutcomeService({target: [partial, partial]})

    first = CheckpointingService(service).run(request(checkpoint, target), object())
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    second = CheckpointingService(service).run(request(checkpoint, target), object())

    assert first.records and second.records
    assert payload["completed_targets"] == []
    assert service.calls == [target, target]


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
    second = "https://www.facebook.com/groups/150"
    third = "https://www.facebook.com/groups/200"
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


@pytest.mark.parametrize("stop", [CrawlCancelled(), JobBudgetReached()])
def test_checkpoint_saves_completed_records_then_reraises_typed_stop(tmp_path: Path, stop: RuntimeError) -> None:
    checkpoint = tmp_path / "typed-stop.json"
    first = "https://www.facebook.com/groups/100"
    second = "https://www.facebook.com/groups/200"
    service = OutcomeService({first: [result(record("100", first))], second: [stop]})
    with pytest.raises(type(stop)):
        CheckpointingService(service).run(request(checkpoint, first, second), object())
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["completed_targets"]
    assert payload["users"][0]["user_id"] == "100"


@pytest.mark.parametrize("stop", [CrawlCancelled(), JobBudgetReached()])
def test_checkpoint_save_failure_does_not_replace_typed_stop(tmp_path: Path, stop: RuntimeError, monkeypatch) -> None:
    target = "https://www.facebook.com/groups/100"
    service = OutcomeService({target: [stop]})
    monkeypatch.setattr("fb_crawl.services.checkpoint.JsonCheckpointStore.save", lambda self, payload: (_ for _ in ()).throw(OSError("private path")))
    with pytest.raises(type(stop)) as captured:
        CheckpointingService(service).run(request(tmp_path / "failed-save.json", target), object())
    assert captured.value.checkpoint_issue.code == "authenticated_checkpoint_save_failed"
    assert captured.value.checkpoint_snapshot.payload["users"] == ()
    assert "private path" not in str(captured.value.checkpoint_issue)


@pytest.mark.parametrize("stop", [CrawlCancelled(), JobBudgetReached()])
def test_checkpoint_failure_snapshot_is_immutable_and_retains_known_state(tmp_path: Path, stop: RuntimeError, monkeypatch) -> None:
    first = "https://www.facebook.com/groups/100"
    second = "https://www.facebook.com/groups/150"
    third = "https://www.facebook.com/groups/200"
    original_save = __import__("fb_crawl.services.checkpoint", fromlist=["JsonCheckpointStore"]).JsonCheckpointStore.save
    calls = 0
    def save_then_fail(store, payload):
        nonlocal calls
        calls += 1
        if calls < 3:
            return original_save(store, payload)
        raise OSError("private checkpoint detail")
    monkeypatch.setattr("fb_crawl.services.checkpoint.JsonCheckpointStore.save", save_then_fail)
    issue = issue_result(second, retryable=False)
    second_result = replace(result(record("150", second)), issues=issue.issues)
    service = OutcomeService({first: [result(record("100", first))], second: [second_result], third: [stop]})
    request_value = request(tmp_path / "retained.json", first, second, third)
    with pytest.raises(type(stop)) as captured:
        CheckpointingService(service).run(request_value, object())
    assert captured.value is stop
    snapshot = stop.checkpoint_snapshot.payload
    assert len(snapshot["completed_targets"]) == 1
    assert [user["user_id"] for user in snapshot["users"]] == ["100", "150"]
    assert snapshot["issues"][0]["code"] == "authenticated_navigation_failed"
    with pytest.raises(TypeError): snapshot["new"] = "value"
    with pytest.raises(TypeError): snapshot["issues"][0]["code"] = "changed"
    assert "private checkpoint detail" not in str(stop)
    assert "private checkpoint detail" not in repr(stop.__dict__)


@pytest.mark.parametrize("stop", [CrawlCancelled(), JobBudgetReached()])
def test_checkpoint_failed_save_preserves_exact_sanitized_stop_snapshot(
    tmp_path: Path, stop: RuntimeError, monkeypatch
) -> None:
    first = "https://www.facebook.com/groups/100"
    issue_target = "https://www.facebook.com/groups/150"
    stopped = "https://www.facebook.com/groups/200"
    raw_error = "raw persistence error must never escape"
    original_save = __import__(
        "fb_crawl.services.checkpoint", fromlist=["JsonCheckpointStore"]
    ).JsonCheckpointStore.save
    save_calls = 0

    def save_then_fail(store, payload):
        nonlocal save_calls
        save_calls += 1
        if save_calls < 3:
            return original_save(store, payload)
        raise OSError(raw_error)

    monkeypatch.setattr(
        "fb_crawl.services.checkpoint.JsonCheckpointStore.save", save_then_fail
    )
    known_issue = issue_result(issue_target, retryable=False).issues[0]
    issue_result_value = replace(
        result(record("150", issue_target)), issues=(known_issue,)
    )
    first_record = replace(
        record("100", first),
        phone_evidence=(
            PhoneEvidence(
                value="0912 345 678",
                source="facebook:post_text",
                source_url="https://www.facebook.com/example/posts/1",
                captured_at="2026-08-21T00:00:00+00:00",
            ),
        ),
    )
    service = OutcomeService(
        {
            first: [result(first_record)],
            issue_target: [issue_result_value],
            stopped: [stop],
        }
    )

    with pytest.raises(type(stop)) as captured:
        CheckpointingService(service).run(
            request(tmp_path / "interrupted.json", first, issue_target, stopped),
            object(),
        )

    assert captured.value is stop
    assert type(captured.value) is type(stop)
    assert stop.__cause__ is None
    assert stop.__context__ is None
    assert stop.checkpoint_issue.code == "authenticated_checkpoint_save_failed"
    assert stop.checkpoint_issue.target == (
        "https://www.facebook.com/groups/200/members"
    )
    assert stop.checkpoint_issue.retryable is True
    payload = stop.checkpoint_snapshot.payload
    assert payload["completed_targets"] == (
        "members:https://www.facebook.com/groups/100/members",
    )
    assert [item["user_id"] for item in payload["users"]] == ["100", "150"]
    assert payload["issues"] == (
        {
            "code": "authenticated_navigation_failed",
            "message": "Authenticated target failed.",
            "target": issue_target,
            "mode": ScrapeMode.AUTHENTICATED,
            "action": AuthenticatedAction.MEMBERS.value,
            "retryable": False,
        },
    )
    with pytest.raises(TypeError):
        payload["completed_targets"] = ()
    with pytest.raises(TypeError):
        payload["users"][0]["user_id"] = "changed"
    with pytest.raises(TypeError):
        payload["users"][0]["phone_evidence"][0]["value"] = "changed"
    with pytest.raises(TypeError):
        payload["issues"][0]["code"] = "changed"
    assert raw_error not in str(stop)
    assert raw_error not in repr(stop)
    assert raw_error not in repr(stop.checkpoint_snapshot)
    assert all(raw_error not in repr(value) for value in vars(stop).values())


def test_checkpoint_reraises_account_safety_without_retry_issue(tmp_path: Path) -> None:
    target = "https://www.facebook.com/groups/100"
    stop = AccountSafetyStop(SafetySignal(SafetyCode.CHECKPOINT, "Facebook checkpoint requires manual review.", True))
    service = OutcomeService({target: [stop]})
    with pytest.raises(AccountSafetyStop):
        CheckpointingService(service).run(request(tmp_path / "safety.json", target), object())
    assert service.calls == [target]
