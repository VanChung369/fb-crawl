from datetime import UTC, datetime, timedelta

from fb_crawl.contacts.postgres import PostgresContactRepository
from fb_crawl.contacts.models import LookupOutcome
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.services.pipeline import EnrichedUser


NOW = datetime(2026, 8, 30, 10, tzinfo=UTC)


class LeaseCursor:
    def __init__(
        self,
        rows: list[tuple[object, ...] | None],
        *,
        state_row: tuple[object, ...] | None = None,
        event_row: tuple[object, ...] | None = None,
        identity_row: tuple[object, ...] | None = None,
    ) -> None:
        self.rows = rows
        self.state_row = state_row
        self.event_row = event_row
        self.identity_row = identity_row
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
        elif "FROM lookup_events" in sql:
            self._row = self.event_row
        elif "FROM facebook_users" in sql:
            self._row = self.identity_row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


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


def test_wait_for_state_accepts_a_publish_after_the_event_boundary() -> None:
    published_at = NOW + timedelta(seconds=1)
    cursor = LeaseCursor(
        [],
        state_row=(
            ProviderStatus.NOT_FOUND.value,
            NOW - timedelta(seconds=5),
            NOW + timedelta(days=7),
            91,
            published_at,
        ),
    )
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
        monotonic=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    state = repository.wait_for_state(
        41,
        "fbnumber",
        "phone",
        NOW,
        timeout_seconds=0,
    )

    assert state is not None
    assert state.updated_at == published_at


def test_lookup_event_read_is_scoped_to_the_owning_account() -> None:
    event_row = (
        71,
        5,
        None,
        41,
        "100123",
        "sample.user",
        "https://www.facebook.com/sample.user",
        "processing",
        "none",
        False,
        False,
        None,
        NOW,
        None,
        501,
        "+84981234567",
        NOW,
    )
    cursor = LeaseCursor([], event_row=event_row)
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
    )

    event = repository.get_lookup_event(5, 71)

    assert event is not None
    assert event.outcome is LookupOutcome.PROCESSING
    assert event.revealed_phone_number_id == 501
    assert event.revealed_phone == "+84981234567"
    query = next(
        (sql, params)
        for sql, params in cursor.commands
        if "FROM lookup_events" in sql
    )
    assert "WHERE events.id = %s AND events.account_id = %s" in query[0]
    assert query[1] == (71, 5)
    assert "account_contact_reveals" not in query[0]
    assert "phone_numbers" in query[0]


def test_contact_identity_read_returns_canonical_enriched_values() -> None:
    cursor = LeaseCursor(
        [],
        identity_row=(
            "100123",
            "sample.user",
            "Canonical Name",
            "https://www.facebook.com/sample.user",
        ),
    )
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
    )

    contact = repository.get_identity(41)

    assert contact.id == 41
    assert contact.identity.name == "Canonical Name"
    assert contact.identity.profile_url == (
        "https://www.facebook.com/sample.user"
    )


def test_provider_state_update_requires_a_live_matching_lease_owner() -> None:
    cursor = LeaseCursor([])
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
    )

    state = repository.update_provider_state(
        41,
        "fbnumber",
        "phone",
        ProviderStatus.FOUND,
        NOW,
        NOW + timedelta(days=30),
        owner_token="stale-owner",
    )

    assert state is None
    lease_check = next(
        (sql, params)
        for sql, params in cursor.commands
        if "FROM enrichment_leases" in sql
    )
    assert "leases.owner_token = %s" in lease_check[0]
    assert "leases.leased_until > statement_timestamp()" in lease_check[0]
    assert "FOR UPDATE" in lease_check[0]
    assert not any(
        "INSERT INTO provider_lookup_state" in sql
        for sql, _params in cursor.commands
    )


class FinalizationCursor(LeaseCursor):
    def __init__(self, *, owns_lease: bool) -> None:
        super().__init__([])
        self.owns_lease = owns_lease
        self._rows: list[tuple[object, ...]] = []

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> None:
        self.commands.append((sql, params))
        self._row = None
        self._rows = []
        if "FROM enrichment_leases" in sql and "FOR UPDATE" in sql:
            self._row = (41,) if self.owns_lease else None
        elif "FROM facebook_users" in sql and "FOR UPDATE" in sql:
            self._rows = [(41,)]
        elif "UPDATE facebook_users" in sql:
            self._row = (41,)
        elif "INSERT INTO phone_numbers" in sql:
            self._row = (501,)
        elif "INSERT INTO enrichment_attempts" in sql:
            self._row = (901,)
        elif "INSERT INTO provider_lookup_state" in sql:
            self._row = (
                ProviderStatus.FOUND.value,
                NOW,
                NOW + timedelta(days=30),
                901,
                NOW,
            )
        elif "DELETE FROM enrichment_leases" in sql:
            self._row = (1,)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


def enriched_result() -> EnrichedUser:
    evidence = PhoneEvidence(
        phone_number="0981234567",
        normalized_phone="+84981234567",
        source="external:fbnumber",
        captured_at=NOW,
        provider="fbnumber",
    )
    return EnrichedUser(
        bundle=UserBundle(
            identity=FacebookIdentity(
                uid="100123", username="sample.user"
            ),
            evidence=(evidence,),
        ),
        provider_result=ProviderResult(
            provider="fbnumber",
            status=ProviderStatus.FOUND,
            evidence=(evidence,),
            checked_at=NOW,
        ),
    )


def test_stale_owner_cannot_persist_any_enrichment_data() -> None:
    cursor = FinalizationCursor(owns_lease=False)
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
    )

    state = repository.finalize_enrichment(
        41,
        "fbnumber",
        "phone",
        "owner-old",
        enriched_result(),
        NOW + timedelta(days=30),
    )

    assert state is None
    assert not any(
        "INSERT INTO user_phone_evidence" in sql
        or "INSERT INTO enrichment_attempts" in sql
        for sql, _params in cursor.commands
    )


def test_owner_finalizes_evidence_attempt_state_and_lease_in_one_transaction() -> None:
    cursor = FinalizationCursor(owns_lease=True)
    repository = PostgresContactRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: LeaseConnection(cursor),
    )

    state = repository.finalize_enrichment(
        41,
        "fbnumber",
        "phone",
        "owner-current",
        enriched_result(),
        NOW + timedelta(days=30),
    )

    assert state is not None
    assert state.latest_attempt_id == 901
    commands = [sql for sql, _params in cursor.commands]
    lease_lock = next(i for i, sql in enumerate(commands) if "FOR UPDATE" in sql)
    evidence = next(
        i for i, sql in enumerate(commands) if "INSERT INTO user_phone_evidence" in sql
    )
    state_write = next(
        i for i, sql in enumerate(commands) if "INSERT INTO provider_lookup_state" in sql
    )
    release = next(
        i for i, sql in enumerate(commands) if "DELETE FROM enrichment_leases" in sql
    )
    assert lease_lock < evidence < state_write < release
    state_sql = commands[state_write]
    assert "GREATEST(statement_timestamp(), %s)" in state_sql
