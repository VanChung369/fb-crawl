from fb_crawl.core.exceptions import (
    IdentityResolutionError,
    RateLimitError,
    SessionError,
)
from fb_crawl.core.models import ProfileIdentity
from fb_crawl.services.identity_repair import (
    IdentityRepairService,
    needs_identity_repair,
)


FIELDS = (
    "user_id",
    "name",
    "username",
    "profile_url",
    "phone_numbers",
    "source",
    "source_url",
    "identity_status",
    "identity_source",
    "identity_error_code",
    "identity_error_message",
)


def row(**changes: str) -> dict[str, str]:
    value = {field: "" for field in FIELDS}
    value.update(
        {
            "user_id": "61573323749006",
            "name": "174 friends",
            "profile_url": (
                "https://www.facebook.com/profile.php?id=61573323749006"
            ),
            "phone_numbers": "+84123",
            "source": "friends",
        }
    )
    value.update(changes)
    return value


class Session:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.assert_calls = 0

    def ensure_authenticated(self, browser) -> None:
        self.ensure_calls += 1

    def assert_authenticated(self, browser) -> None:
        self.assert_calls += 1


class Resolver:
    def __init__(self, outcomes) -> None:
        self.outcomes = iter(outcomes)
        self.records = []

    def resolve(self, browser, record):
        self.records.append(record)
        outcome = next(self.outcomes)

        if isinstance(outcome, BaseException):
            raise outcome

        return outcome


def identity(
    *,
    name: str = "Hiếu Văn",
    username: str | None = None,
) -> ProfileIdentity:
    return ProfileIdentity(
        user_id="61573323749006",
        name=name,
        username=username,
        profile_url=(
            f"https://www.facebook.com/{username}"
            if username
            else "https://www.facebook.com/profile.php?id=61573323749006"
        ),
    )


def test_repair_replaces_social_count_name_and_preserves_other_fields() -> None:
    session = Session()
    service = IdentityRepairService(
        session,
        Resolver([identity()]),
        sleep_func=lambda seconds: None,
    )

    result = service.run(FIELDS, [row()], object(), delay_seconds=0)

    assert result.rows[0]["name"] == "Hiếu Văn"
    assert result.rows[0]["username"] == ""
    assert result.rows[0]["phone_numbers"] == "+84123"
    assert result.rows[0]["identity_status"] == "repaired"
    assert result.rows[0]["identity_source"] == "facebook:profile"
    assert result.stats.repaired == 1
    assert session.ensure_calls == 1


def test_verified_numeric_profile_can_legitimately_have_no_username() -> None:
    existing = row(name="Hiếu Văn")
    result = IdentityRepairService(
        Session(),
        Resolver([identity()]),
        sleep_func=lambda seconds: None,
    ).run(FIELDS, [existing], object(), delay_seconds=0)

    assert result.rows[0]["identity_status"] == "verified"
    assert result.rows[0]["username"] == ""
    assert needs_identity_repair(result.rows[0]) is False


def test_failed_rows_require_retry_failed_or_force() -> None:
    failed = row(identity_status="failed")

    assert needs_identity_repair(failed) is False
    assert needs_identity_repair(failed, retry_failed=True) is True
    assert needs_identity_repair(failed, force=True) is True


def test_resolution_failure_is_sanitized_and_can_be_retried() -> None:
    raw_error = IdentityResolutionError(
        "Authenticated profile identity resolution failed."
    )
    result = IdentityRepairService(
        Session(),
        Resolver([raw_error]),
        sleep_func=lambda seconds: None,
    ).run(FIELDS, [row()], object(), delay_seconds=0)

    repaired = result.rows[0]
    assert repaired["identity_status"] == "failed"
    assert repaired["identity_error_code"] == (
        "authenticated_identity_resolution_failed"
    )
    assert "profile identity" in repaired["identity_error_message"]
    assert result.stats.failed == 1


def test_limit_leaves_pending_rows_and_only_delays_between_attempts() -> None:
    sleeps = []
    result = IdentityRepairService(
        Session(),
        Resolver([identity(), identity()]),
        sleep_func=sleeps.append,
    ).run(FIELDS, [row(), row(), row()], object(), limit=2, delay_seconds=1.5)

    assert result.stats.eligible == 3
    assert result.stats.attempted == 2
    assert result.stats.pending == 1
    assert sleeps == [1.5]


def test_good_completed_rows_do_not_start_authenticated_session() -> None:
    session = Session()
    result = IdentityRepairService(
        session,
        Resolver([]),
    ).run(
        FIELDS,
        [
            row(
                name="Hiếu Văn",
                identity_status="verified",
            )
        ],
        object(),
    )

    assert result.stats.attempted == 0
    assert session.ensure_calls == 0


def test_retryable_failure_uses_exponential_backoff_and_then_succeeds() -> None:
    sleeps = []
    resolver = Resolver(
        [
            IdentityResolutionError("Temporary profile parse failure."),
            identity(),
        ]
    )
    result = IdentityRepairService(
        Session(),
        resolver,
        sleep_func=sleeps.append,
        jitter_func=lambda low, high: high / 2,
    ).run(
        FIELDS,
        [row()],
        object(),
        delay_seconds=0,
        max_retries=1,
        retry_backoff_seconds=2,
        retry_jitter_seconds=0.5,
    )

    assert result.stats.retried == 1
    assert result.stats.repaired == 1
    assert len(resolver.records) == 2
    assert sleeps == [2.25]


def test_rate_limit_is_counted_and_retried() -> None:
    result = IdentityRepairService(
        Session(),
        Resolver(
            [
                RateLimitError("Facebook temporarily limited requests."),
                identity(),
            ]
        ),
        sleep_func=lambda seconds: None,
        jitter_func=lambda low, high: 0,
    ).run(
        FIELDS,
        [row()],
        object(),
        delay_seconds=0,
        max_retries=1,
    )

    assert result.stats.rate_limited == 1
    assert result.stats.retried == 1
    assert result.stats.repaired == 1


def test_progress_is_checkpointed_before_and_after_each_profile() -> None:
    statuses = []
    IdentityRepairService(
        Session(),
        Resolver([identity()]),
        sleep_func=lambda seconds: None,
    ).run(
        FIELDS,
        [row()],
        object(),
        delay_seconds=0,
        progress_func=lambda result: statuses.append(
            result.rows[0]["identity_status"]
        ),
    )

    assert statuses == ["running", "repaired"]


def test_keyboard_interrupt_is_persisted_as_resumable_state() -> None:
    snapshots = []
    result = IdentityRepairService(
        Session(),
        Resolver([KeyboardInterrupt()]),
        sleep_func=lambda seconds: None,
    ).run(
        FIELDS,
        [row()],
        object(),
        delay_seconds=0,
        progress_func=lambda result: snapshots.append(result),
    )

    assert result.stats.interrupted == 1
    assert result.stats.pending == 1
    assert result.rows[0]["identity_status"] == "interrupted"
    assert needs_identity_repair(result.rows[0]) is True
    assert snapshots[-1].rows[0]["identity_status"] == "interrupted"


def test_session_loss_stops_run_and_leaves_current_row_resumable() -> None:
    class ExpiredSession(Session):
        def assert_authenticated(self, browser) -> None:
            raise SessionError("Authenticated session unavailable.")

    result = IdentityRepairService(
        ExpiredSession(),
        Resolver([identity()]),
    ).run(FIELDS, [row(), row()], object(), delay_seconds=0)

    assert result.stats.session_failed == 1
    assert result.stats.attempted == 1
    assert result.stats.pending == 2
    assert result.rows[0]["identity_status"] == "interrupted"
    assert result.rows[1]["identity_status"] == ""
