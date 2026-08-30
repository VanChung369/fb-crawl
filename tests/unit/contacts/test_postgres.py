from datetime import UTC, datetime, timedelta

from fb_crawl.contacts.postgres import PostgresContactRepository
from fb_data_pipeline.core.models import ProviderStatus


NOW = datetime(2026, 8, 30, 10, tzinfo=UTC)


class LeaseCursor:
    def __init__(
        self,
        rows: list[tuple[object, ...] | None],
        *,
        state_row: tuple[object, ...] | None = None,
    ) -> None:
        self.rows = rows
        self.state_row = state_row
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []
        self._row: tuple[object, ...] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> None:
        self.commands.append((sql, params))
        self._row = None
        if "INSERT INTO enrichment_leases" in sql:
            self._row = self.rows.pop(0)
        elif "FROM provider_lookup_state" in sql:
            self._row = self.state_row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class LeaseConnection:
    def __init__(self, cursor: LeaseCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> LeaseCursor:
        return self._cursor


def test_only_one_owner_claims_a_live_lease_with_one_atomic_statement() -> None:
    expires = NOW + timedelta(seconds=30)
    cursor = LeaseCursor([("owner-a", expires), None])
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
    )

    first = repository.claim_lease(
        41, "fbnumber", "phone", "owner-a", NOW, expires
    )
    second = repository.claim_lease(
        41, "fbnumber", "phone", "owner-b", NOW, expires
    )

    assert (first.acquired, second.acquired) == (True, False)
    claims = [
        (sql, params)
        for sql, params in cursor.commands
        if "INSERT INTO enrichment_leases" in sql
    ]
    assert len(claims) == 2
    assert "ON CONFLICT (facebook_user_id, provider, field) DO UPDATE" in claims[0][0]
    assert "enrichment_leases.leased_until <= %s" in claims[0][0]
    assert claims[0][1] == (
        41,
        "fbnumber",
        "phone",
        "owner-a",
        expires,
        NOW,
    )


def test_wait_for_state_does_not_accept_the_preexisting_check_boundary() -> None:
    cursor = LeaseCursor(
        [],
        state_row=(
            ProviderStatus.NOT_FOUND.value,
            NOW,
            NOW + timedelta(days=7),
            91,
            NOW,
        ),
    )
    clock = iter((0.0, 0.25))
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
        monotonic=lambda: next(clock),
        sleeper=lambda _seconds: None,
    )

    state = repository.wait_for_state(
        41,
        "fbnumber",
        "phone",
        NOW,
        timeout_seconds=0.25,
    )

    assert state is None
