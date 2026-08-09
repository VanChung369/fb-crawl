from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
import pytest

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProfileData,
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.repositories.errors import (
    DatabaseError,
    DatabaseIdentityConflict,
)
from fb_data_pipeline.repositories.postgres import PostgresRepository
from fb_data_pipeline.services.pipeline import EnrichedUser


class RecordingCursor:
    def __init__(
        self,
        *,
        matching_user_ids: list[list[tuple[int]]] | None = None,
        retry_rows: list[tuple[object, ...]] | None = None,
        lock_acquired: bool = True,
        inserted_user_id: int | None = 41,
        fail_on: str = "",
    ) -> None:
        self.matching_user_ids = matching_user_ids or [[]]
        self.retry_rows = retry_rows or []
        self.lock_acquired = lock_acquired
        self.inserted_user_id = inserted_user_id
        self.fail_on = fail_on
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []
        self._rows: list[tuple[object, ...]] = []
        self._row: tuple[object, ...] | None = None
        self._phone_ids: dict[str, int] = {}
        self.closed = False

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True
        return None

    def close(self) -> None:
        self.closed = True

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> None:
        self.commands.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("database write failed")

        self._rows = []
        self._row = None
        if "pg_try_advisory_lock" in sql:
            self._row = (self.lock_acquired,)
        elif "pg_advisory_unlock" in sql:
            self._row = (True,)
        elif "WITH latest AS" in sql:
            self._rows = list(self.retry_rows)
        elif "FROM facebook_users" in sql and "FOR UPDATE" in sql:
            self._rows = list(self.matching_user_ids.pop(0))
        elif "INSERT INTO facebook_users" in sql:
            if self.inserted_user_id is not None:
                self._row = (self.inserted_user_id,)
        elif "UPDATE facebook_users" in sql:
            assert params is not None
            self._row = (params[-1],)
        elif "INSERT INTO phone_numbers" in sql:
            assert params is not None
            normalized = str(params[0])
            phone_id = self._phone_ids.setdefault(
                normalized,
                100 + len(self._phone_ids),
            )
            self._row = (phone_id,)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self.recording_cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.autocommit = False
        self.closed = False

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        self.closed = True

    def cursor(self) -> RecordingCursor:
        return self.recording_cursor

    def close(self) -> None:
        self.closed = True


def connection_factory(
    connection: RecordingConnection,
) -> Callable[[str], RecordingConnection]:
    def connect(_database_url: str) -> RecordingConnection:
        return connection

    return connect


def make_enriched(
    *,
    status: ProviderStatus = ProviderStatus.FOUND,
    include_evidence: bool = True,
    profile: ProfileData | None = None,
) -> EnrichedUser:
    captured_at = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)
    crawler = PhoneEvidence(
        phone_number="0912345678",
        normalized_phone="+84912345678",
        source="profile_about",
        source_url=(
            "https://www.facebook.com/"
            "thang.duc.961556/about_contact_and_basic_info"
        ),
        captured_at=captured_at,
        confidence="profile_field",
    )
    provider = PhoneEvidence(
        phone_number="0987654321",
        normalized_phone="+84987654321",
        source="external:fbnumber",
        captured_at=captured_at,
        provider="fbnumber",
        correlation_id="request-123",
        confidence="provider_result",
    )
    evidence = (provider, crawler) if include_evidence else ()
    provider_evidence = (provider,) if include_evidence else ()
    return EnrichedUser(
        bundle=UserBundle(
            identity=FacebookIdentity(
                uid="100013347102233",
                username="thang.duc.961556",
                name="Bui Duc Thang",
                profile_url=(
                    "https://www.facebook.com/thang.duc.961556"
                ),
            ),
            evidence=evidence,
            profile=profile or ProfileData(),
        ),
        provider_result=ProviderResult(
            provider="fbnumber",
            status=status,
            evidence=provider_evidence,
            checked_at=captured_at,
            correlation_id="request-123",
            error_code=("provider_failed" if status is ProviderStatus.FAILED else ""),
        ),
    )


def test_repository_inserts_user_evidence_and_attempt_in_stable_order() -> None:
    cursor = RecordingCursor()
    connection = RecordingConnection(cursor)
    enriched = make_enriched()
    repository = PostgresRepository(
        "postgresql://hidden",
        statement_timeout_seconds=7.5,
        connect_factory=connection_factory(connection),
    )

    assert repository.save_enriched_user(enriched) == 41
    assert connection.committed is True
    assert not any(
        "INSERT INTO facebook_user_profiles" in sql
        for sql, _params in cursor.commands
    )

    timeout = cursor.commands[0]
    assert "set_config('statement_timeout'" in timeout[0]
    assert timeout[1] == ("7500ms",)

    lock_params = [
        params
        for sql, params in cursor.commands
        if "pg_advisory_xact_lock" in sql
    ]
    assert lock_params == [
        (alias,) for alias in sorted(enriched.bundle.identity.aliases)
    ]

    phone_params = [
        params
        for sql, params in cursor.commands
        if "INSERT INTO phone_numbers" in sql
    ]
    assert [params[0] for params in phone_params if params] == [
        "+84912345678",
        "+84987654321",
    ]

    evidence_params = [
        params
        for sql, params in cursor.commands
        if "INSERT INTO user_phone_evidence" in sql
    ]
    assert [params[2] for params in evidence_params if params] == [
        "fb_crawl",
        "fbnumber",
    ]
    attempt = next(
        params
        for sql, params in cursor.commands
        if "INSERT INTO enrichment_attempts" in sql
    )
    assert attempt == (
        41,
        "fbnumber",
        "found",
        datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
        "request-123",
        "",
        1,
    )


def test_repository_updates_one_matching_identity() -> None:
    cursor = RecordingCursor(matching_user_ids=[[(12,)]])
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    assert repository.save_enriched_user(make_enriched()) == 12
    update = next(
        params
        for sql, params in cursor.commands
        if "UPDATE facebook_users" in sql
    )
    assert update == (
        "100013347102233",
        "thang.duc.961556",
        "thang.duc.961556",
        "Bui Duc Thang",
        "https://www.facebook.com/thang.duc.961556",
        12,
    )


def test_repository_rolls_back_when_aliases_match_multiple_users() -> None:
    cursor = RecordingCursor(matching_user_ids=[[(11,), (12,)]])
    connection = RecordingConnection(cursor)
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(connection),
    )

    with pytest.raises(DatabaseIdentityConflict):
        repository.save_enriched_user(make_enriched())

    assert connection.rolled_back is True
    assert not any(
        "INSERT INTO phone_numbers" in sql for sql, _params in cursor.commands
    )


@pytest.mark.parametrize(
    "status",
    [
        ProviderStatus.NOT_FOUND,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.FAILED,
    ],
)
def test_repository_records_attempt_without_phone_evidence(
    status: ProviderStatus,
) -> None:
    cursor = RecordingCursor()
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    repository.save_enriched_user(
        make_enriched(status=status, include_evidence=False)
    )

    attempt = next(
        params
        for sql, params in cursor.commands
        if "INSERT INTO enrichment_attempts" in sql
    )
    assert attempt is not None
    assert attempt[2] == status.value
    assert attempt[-1] == 0


def test_repository_rolls_back_when_attempt_write_fails() -> None:
    cursor = RecordingCursor(fail_on="INSERT INTO enrichment_attempts")
    connection = RecordingConnection(cursor)
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(connection),
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        repository.save_enriched_user(make_enriched())

    assert connection.rolled_back is True


def test_repository_upserts_profile_before_phone_evidence() -> None:
    observed_at = datetime(2026, 8, 9, 5, 0, tzinfo=UTC)
    cursor = RecordingCursor()
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    repository.save_enriched_user(
        make_enriched(
            profile=ProfileData(
                address="Ha Noi",
                birth_date="12 thang 8, 1990",
                gender="Nam",
                source_url="https://www.facebook.com/a.user/about",
                observed_at=observed_at,
            )
        )
    )

    profile_index = next(
        index
        for index, (sql, _params) in enumerate(cursor.commands)
        if "INSERT INTO facebook_user_profiles" in sql
    )
    phone_index = next(
        index
        for index, (sql, _params) in enumerate(cursor.commands)
        if "INSERT INTO phone_numbers" in sql
    )
    assert profile_index < phone_index
    assert cursor.commands[profile_index][1] == (
        41,
        "Ha Noi",
        "12 thang 8, 1990",
        "Nam",
        "https://www.facebook.com/a.user/about",
        observed_at,
    )


def test_repository_rolls_back_when_profile_write_fails() -> None:
    cursor = RecordingCursor(fail_on="INSERT INTO facebook_user_profiles")
    connection = RecordingConnection(cursor)
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(connection),
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        repository.save_enriched_user(
            make_enriched(profile=ProfileData(address="Ha Noi"))
        )

    assert connection.rolled_back is True
    assert not any(
        "INSERT INTO phone_numbers" in sql for sql, _params in cursor.commands
    )


def test_repository_hides_driver_connection_details() -> None:
    def unavailable(_database_url: str):
        raise psycopg.OperationalError("secret DSN and password")

    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=unavailable,
    )

    with pytest.raises(DatabaseError) as captured:
        repository.save_enriched_user(make_enriched())

    assert captured.value.safe_message == "Database operation failed."
    assert "secret" not in captured.value.safe_message


def test_fbnumber_retry_lock_is_nonblocking_autocommit_and_released() -> None:
    cursor = RecordingCursor(lock_acquired=True)
    connection = RecordingConnection(cursor)
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(connection),
    )

    with repository.fbnumber_retry_lock() as acquired:
        assert acquired is True
        assert connection.autocommit is True

    sql = [command for command, _params in cursor.commands]
    assert any("pg_try_advisory_lock" in command for command in sql)
    assert any("pg_advisory_unlock" in command for command in sql)
    assert connection.closed is True
    assert cursor.closed is True


def test_busy_fbnumber_retry_lock_is_not_unlocked_by_non_owner() -> None:
    cursor = RecordingCursor(lock_acquired=False)
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    with repository.fbnumber_retry_lock() as acquired:
        assert acquired is False

    assert not any(
        "pg_advisory_unlock" in sql for sql, _params in cursor.commands
    )


def test_repository_maps_latest_retry_candidates() -> None:
    checked_at = datetime(2026, 8, 8, tzinfo=UTC)
    cursor = RecordingCursor(
        retry_rows=[
            (
                7,
                "100",
                "sample.user",
                "Sample User",
                "https://www.facebook.com/sample.user",
                "failed",
                checked_at,
                "provider_transport_error",
            )
        ]
    )
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    candidates = repository.list_fbnumber_retry_candidates(
        eligible_before=checked_at,
        limit=20,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.user_id == 7
    assert candidate.identity == FacebookIdentity(
        uid="100",
        username="sample.user",
        name="Sample User",
        profile_url="https://www.facebook.com/sample.user",
    )
    assert candidate.status is ProviderStatus.FAILED
    assert candidate.checked_at == checked_at
    assert candidate.error_code == "provider_transport_error"
    query = next(
        item for item in cursor.commands if "WITH latest AS" in item[0]
    )
    assert query[1] == ("fbnumber", checked_at, checked_at, 20)


def test_repository_force_selection_passes_no_cutoff() -> None:
    cursor = RecordingCursor()
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    assert repository.list_fbnumber_retry_candidates(
        eligible_before=None,
        limit=3,
    ) == ()

    query = next(
        item for item in cursor.commands if "WITH latest AS" in item[0]
    )
    assert query[1] == ("fbnumber", None, None, 3)


def test_repository_rejects_nonpositive_retry_limit_before_connecting() -> None:
    def unexpected_connection(_database_url: str):
        raise AssertionError("database connection should not be opened")

    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=unexpected_connection,
    )

    with pytest.raises(ValueError, match="limit must be positive"):
        repository.list_fbnumber_retry_candidates(
            eligible_before=None,
            limit=0,
        )
