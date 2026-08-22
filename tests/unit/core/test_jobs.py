from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import (
    AccountStatus,
    CanonicalJobTarget,
    CrawlEvent,
    JobCreateCommand,
    JobStatus,
    KeysetCursor,
    SafeJobOptions,
    SafetyCode,
    TargetStatus,
    can_transition_job,
    can_transition_target,
    canonical_job_target,
    canonical_request_fingerprint,
    decode_cursor,
    encode_cursor,
    is_terminal_job_status,
    is_terminal_target_status,
)
from fb_crawl.core.models import AuthenticatedAction, ProfileField


def test_status_enums_match_the_job_orchestration_schema() -> None:
    """Break caught: a new model value would not be accepted by migration 003."""
    assert {status.value for status in JobStatus} == {
        "queued", "running", "cancelling", "succeeded", "partial", "failed",
        "cancelled", "blocked",
    }
    assert {status.value for status in TargetStatus} == {
        "pending", "running", "succeeded", "partial", "failed", "skipped",
        "cancelled", "blocked",
    }
    assert {status.value for status in AccountStatus} == {
        "ready", "cooldown", "blocked", "manual_review",
    }
    assert {code.value for code in SafetyCode} == {
        "session_expired", "checkpoint", "two_factor", "captcha",
        "temporary_block", "account_restricted", "unusual_activity",
        "account_recovery",
    }


@pytest.mark.parametrize(
    ("current", "next_status", "allowed"),
    [
        (JobStatus.QUEUED, JobStatus.RUNNING, True),
        (JobStatus.RUNNING, JobStatus.CANCELLING, True),
        (JobStatus.RUNNING, JobStatus.SUCCEEDED, True),
        (JobStatus.SUCCEEDED, JobStatus.RUNNING, False),
        (JobStatus.BLOCKED, JobStatus.QUEUED, False),
    ],
)
def test_job_transition_table(
    current: JobStatus, next_status: JobStatus, allowed: bool
) -> None:
    """Break caught: the job state machine permits an invalid lifecycle change."""
    assert can_transition_job(current, next_status) is allowed


@pytest.mark.parametrize(
    ("current", "next_status", "allowed"),
    [
        (TargetStatus.PENDING, TargetStatus.RUNNING, True),
        (TargetStatus.PENDING, TargetStatus.CANCELLED, True),
        (TargetStatus.RUNNING, TargetStatus.PARTIAL, True),
        (TargetStatus.RUNNING, TargetStatus.BLOCKED, True),
        (TargetStatus.SUCCEEDED, TargetStatus.RUNNING, False),
        (TargetStatus.BLOCKED, TargetStatus.PENDING, False),
    ],
)
def test_target_transition_table(
    current: TargetStatus, next_status: TargetStatus, allowed: bool
) -> None:
    """Break caught: the target state machine permits an invalid lifecycle change."""
    assert can_transition_target(current, next_status) is allowed


def test_terminal_status_predicates_distinguish_active_and_finished_work() -> None:
    """Break caught: active states are treated as terminal or vice versa."""
    assert not is_terminal_job_status(JobStatus.CANCELLING)
    assert is_terminal_job_status(JobStatus.SUCCEEDED)
    assert not is_terminal_target_status(TargetStatus.RUNNING)
    assert is_terminal_target_status(TargetStatus.SKIPPED)


def test_safe_options_default_to_the_api_owned_limits() -> None:
    """Break caught: an omitted API option weakens a safe default."""
    options = SafeJobOptions.from_mapping(AuthenticatedAction.MEMBERS, {})
    assert options.steps == 10
    assert options.max_duration_seconds == 300
    assert options.navigation_delay_seconds == 8
    assert options.max_retries == 1
    assert options.depth == 1
    assert options.max_users == 1000
    assert options.profile_limit == 20
    assert options.phone_post_steps == 0


@pytest.mark.parametrize(
    ("action", "raw_options"),
    [
        (AuthenticatedAction.MEMBERS, {"steps": 21}),
        (AuthenticatedAction.MEMBERS, {"max_duration_seconds": 1801}),
        (AuthenticatedAction.MEMBERS, {"navigation_delay_seconds": 7}),
        (AuthenticatedAction.MEMBERS, {"max_retries": 2}),
        (AuthenticatedAction.FRIENDS, {"depth": 3}),
        (AuthenticatedAction.MEMBERS, {"profile_limit": 51}),
        (AuthenticatedAction.FRIENDS, {"max_users": 1001}),
        (
            AuthenticatedAction.MEMBERS,
            {
                "enrich_profiles": True,
                "profile_fields": ["phone"],
                "phone_post_steps": 21,
            },
        ),
        (
            AuthenticatedAction.MEMBERS,
            {
                "enrich_profiles": True,
                "profile_fields": ["phone"],
                "phone_post_duration_seconds": 301,
            },
        ),
        (AuthenticatedAction.MEMBERS, {"force_uid_refresh": True}),
        (AuthenticatedAction.MEMBERS, {"depth": 2}),
        (AuthenticatedAction.MEMBERS, {"max_users": 25}),
    ],
)
def test_safe_options_reject_unsafe_or_action_incompatible_values(
    action: AuthenticatedAction, raw_options: dict[str, object]
) -> None:
    """Break caught: client options bypass an API-owned safety bound."""
    with pytest.raises(ValidationError):
        SafeJobOptions.from_mapping(action, raw_options)


def test_safe_options_normalize_fields_and_build_a_resumable_request() -> None:
    """Break caught: normalized API policy is not carried into browser execution."""
    options = SafeJobOptions.from_mapping(
        AuthenticatedAction.FRIENDS,
        {
            "depth": 2,
            "max_users": 9,
            "enrich_profiles": True,
            "profile_fields": ["phone", "website", "phone"],
            "profile_limit": 7,
            "phone_post_steps": 3,
            "phone_post_duration_seconds": 60,
        },
    )
    assert options.profile_fields == (ProfileField.PHONE, ProfileField.WEBSITE)

    request = options.to_scrape_request(
        AuthenticatedAction.FRIENDS,
        ("https://www.facebook.com/example/friends",),
        checkpoint_path="runtime/checkpoints/jobs/job/target.json",
    )

    assert request.resume is True
    assert request.checkpoint_path == "runtime/checkpoints/jobs/job/target.json"
    assert request.delay_seconds == 8
    assert request.profile_delay_seconds == 8
    assert request.depth == 2
    assert request.max_nodes == 9
    assert request.profile_fields == (ProfileField.PHONE, ProfileField.WEBSITE)
    assert request.profile_limit == 7
    assert request.phone_post_steps == 3
    assert request.phone_post_duration_seconds == 60
    assert request.force_uid_refresh is False


@pytest.mark.parametrize(
    ("action", "raw_url", "expected"),
    [
        (
            AuthenticatedAction.MEMBERS,
            "https://facebook.com/groups/123",
            CanonicalJobTarget(
                target_key="members:https://www.facebook.com/groups/123/members",
                target_url="https://www.facebook.com/groups/123/members",
                target_kind="group_members",
            ),
        ),
        (
            AuthenticatedAction.COMMENTS,
            "https://facebook.com/acme/posts/123?ref=share",
            CanonicalJobTarget(
                target_key="comments:https://www.facebook.com/acme/posts/123",
                target_url="https://www.facebook.com/acme/posts/123",
                target_kind="post_comments",
            ),
        ),
        (
            AuthenticatedAction.PROFILE,
            "https://facebook.com/example?ref=share",
            CanonicalJobTarget(
                target_key="profile:https://www.facebook.com/example",
                target_url="https://www.facebook.com/example",
                target_kind="profile",
            ),
        ),
        (
            AuthenticatedAction.FRIENDS,
            "https://facebook.com/example",
            CanonicalJobTarget(
                target_key="friends:https://www.facebook.com/example/friends",
                target_url="https://www.facebook.com/example/friends",
                target_kind="profile_friends",
            ),
        ),
        (
            AuthenticatedAction.FOLLOWERS,
            "https://facebook.com/example",
            CanonicalJobTarget(
                target_key="followers:https://www.facebook.com/example/followers",
                target_url="https://www.facebook.com/example/followers",
                target_kind="profile_followers",
            ),
        ),
        (
            AuthenticatedAction.REACTIONS,
            "https://facebook.com/acme/posts/123",
            CanonicalJobTarget(
                target_key="reactions:https://www.facebook.com/acme/posts/123",
                target_url="https://www.facebook.com/acme/posts/123",
                target_kind="post_reactions",
            ),
        ),
        (
            AuthenticatedAction.ENGAGEMENT,
            "https://facebook.com/acme/posts/123",
            CanonicalJobTarget(
                target_key="engagement:https://www.facebook.com/acme/posts/123",
                target_url="https://www.facebook.com/acme/posts/123",
                target_kind="post_engagement",
            ),
        ),
    ],
)
def test_canonical_job_target_uses_the_action_specific_normalizer(
    action: AuthenticatedAction, raw_url: str, expected: CanonicalJobTarget
) -> None:
    """Break caught: an API target skips the established action-specific URL rules."""
    assert canonical_job_target(action, raw_url) == expected


@pytest.mark.parametrize(
    ("action", "raw_url"),
    [
        (AuthenticatedAction.MEMBERS, "https://example.test/groups/123/members"),
        (AuthenticatedAction.COMMENTS, "https://facebook.com/groups/123/members"),
        (AuthenticatedAction.PROFILE, "https://facebook.com/login"),
        (AuthenticatedAction.FRIENDS, "https://facebook.com/example/followers"),
        (AuthenticatedAction.FOLLOWERS, "https://facebook.com/example/friends"),
        (AuthenticatedAction.REACTIONS, "file:///acme/posts/123"),
        (AuthenticatedAction.ENGAGEMENT, "https://127.0.0.1/acme/posts/123"),
        (AuthenticatedAction.MEMBERS, "https://user:pass@facebook.com/groups/123"),
    ],
)
def test_canonical_job_target_rejects_unsafe_or_mismatched_urls(
    action: AuthenticatedAction, raw_url: str
) -> None:
    """Break caught: a non-target or unsafe URL is accepted as a crawl target."""
    with pytest.raises(ValidationError):
        canonical_job_target(action, raw_url)


@pytest.mark.parametrize(
    ("action", "raw_url"),
    [
        (AuthenticatedAction.PROFILE, "https://facebook.com/example/friends"),
        (AuthenticatedAction.PROFILE, "https://facebook.com/acme/posts/123"),
        (AuthenticatedAction.FRIENDS, "https://facebook.com/acme/posts/123"),
    ],
)
def test_canonical_job_target_rejects_urls_for_another_authenticated_action(
    action: AuthenticatedAction, raw_url: str
) -> None:
    """Break caught: profile or relationship normalizers silently truncate another action's path."""
    with pytest.raises(
        ValidationError, match="^Invalid authenticated job target\\.$"
    ):
        canonical_job_target(action, raw_url)


def test_canonical_job_target_hides_malformed_url_parser_errors() -> None:
    """Break caught: malformed input leaks a parser ValueError from the API boundary."""
    with pytest.raises(
        ValidationError, match="^Invalid authenticated job target\\.$"
    ):
        canonical_job_target(AuthenticatedAction.MEMBERS, "https://[::1")


def test_request_fingerprint_uses_canonical_ordered_request_data() -> None:
    """Break caught: equivalent create requests receive different idempotency hashes."""
    options_one = SafeJobOptions.from_mapping(
        AuthenticatedAction.MEMBERS,
        {"steps": 10, "enrich_profiles": True, "profile_fields": ["phone"]},
    )
    options_two = SafeJobOptions.from_mapping(
        AuthenticatedAction.MEMBERS,
        {"profile_fields": ["phone"], "enrich_profiles": True, "steps": 10},
    )
    target = canonical_job_target(
        AuthenticatedAction.MEMBERS, "https://facebook.com/groups/123"
    )
    duplicate_target = canonical_job_target(
        AuthenticatedAction.MEMBERS, "https://www.facebook.com/groups/123/members"
    )
    first = JobCreateCommand(AuthenticatedAction.MEMBERS, (target,), options_one)
    equivalent = JobCreateCommand(
        AuthenticatedAction.MEMBERS, (duplicate_target,), options_two
    )
    changed_option = JobCreateCommand(
        AuthenticatedAction.MEMBERS,
        (target,),
        SafeJobOptions.from_mapping(AuthenticatedAction.MEMBERS, {"steps": 11}),
    )
    changed_target = JobCreateCommand(
        AuthenticatedAction.MEMBERS,
        (canonical_job_target(AuthenticatedAction.MEMBERS, "https://facebook.com/groups/456"),),
        options_one,
    )
    changed_action = JobCreateCommand(
        AuthenticatedAction.PROFILE,
        (canonical_job_target(AuthenticatedAction.PROFILE, "https://facebook.com/example"),),
        SafeJobOptions.from_mapping(AuthenticatedAction.PROFILE, {}),
    )

    assert canonical_request_fingerprint(first) == canonical_request_fingerprint(equivalent)
    assert canonical_request_fingerprint(first) != canonical_request_fingerprint(changed_option)
    assert canonical_request_fingerprint(first) != canonical_request_fingerprint(changed_target)
    assert canonical_request_fingerprint(first) != canonical_request_fingerprint(changed_action)


def test_request_fingerprint_normalizes_equivalent_integer_and_float_defaults() -> None:
    """Break caught: semantically equal default options hash differently by JSON number spelling."""
    target = canonical_job_target(
        AuthenticatedAction.MEMBERS, "https://facebook.com/groups/123"
    )
    direct = JobCreateCommand(
        AuthenticatedAction.MEMBERS, (target,), SafeJobOptions()
    )
    mapped = JobCreateCommand(
        AuthenticatedAction.MEMBERS,
        (target,),
        SafeJobOptions.from_mapping(AuthenticatedAction.MEMBERS, {}),
    )

    assert canonical_request_fingerprint(direct) == canonical_request_fingerprint(mapped)


def test_job_command_rejects_more_than_100_raw_targets_before_deduplication() -> None:
    """Break caught: repeated targets bypass the API's 100-input request limit."""
    target = canonical_job_target(
        AuthenticatedAction.MEMBERS, "https://facebook.com/groups/123"
    )
    with pytest.raises(ValidationError, match="^A job requires 1 to 100 targets\\.$"):
        JobCreateCommand(
            AuthenticatedAction.MEMBERS,
            (target,) * 101,
            SafeJobOptions.from_mapping(AuthenticatedAction.MEMBERS, {}),
        )


def test_crawl_event_snapshots_counters_as_an_immutable_mapping() -> None:
    """Break caught: a caller mutates an event's counters after the event is created."""
    counters = {"users_persisted": 1}
    event = CrawlEvent(
        id=1,
        job_id=uuid4(),
        event_type="target_progress",
        level="info",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
        counters=counters,
    )
    counters["users_persisted"] = 99

    assert event.counters == {"users_persisted": 1}
    with pytest.raises(TypeError):
        event.counters["steps_completed"] = 2  # type: ignore[index]


def test_cursor_round_trips_a_utc_timestamp_and_positive_row_id() -> None:
    """Break caught: a list cursor loses ordering information between pages."""
    cursor = KeysetCursor(datetime(2026, 8, 21, 10, 30, tzinfo=UTC), 42)
    assert decode_cursor(encode_cursor(cursor)) == cursor


@pytest.mark.parametrize(
    "value",
    [
        "not-base64",
        base64.urlsafe_b64encode(b"[]").decode(),
        base64.urlsafe_b64encode(b'{"sort_at":"2026-08-21T10:30:00+00:00","row_id":0}').decode(),
        base64.urlsafe_b64encode(b'{"sort_at":"2026-08-21T10:30:00","row_id":1}').decode(),
        base64.urlsafe_b64encode(b"x" * 513).decode(),
    ],
)
def test_cursor_rejects_invalid_or_unsafe_payloads(value: str) -> None:
    """Break caught: malformed pagination input is accepted or leaks parse details."""
    with pytest.raises(ValidationError, match="^Invalid pagination cursor\\.$"):
        decode_cursor(value)
