from dataclasses import replace
from pathlib import Path

import pytest

from fb_crawl.core.exceptions import SessionError, ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeMode,
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
