from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fb_crawl.entitlements.quota import (
    ContactQuotaService,
    PostgresContactQuotaRepository,
    QuotaPrecheck,
    RevealDecision,
)


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
PERIOD = date(2026, 8, 1)


class MemoryQuotaRepository:
    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self.used = 0
        self.reveals: dict[tuple[int, date, int], int] = {}

    def precheck(
        self, account_id: int, facebook_user_id: int, period_start: date, now: datetime
    ) -> QuotaPrecheck:
        existing = (account_id, period_start, facebook_user_id) in self.reveals
        return QuotaPrecheck(existing or self.used < self.limit, existing, self.used, self.limit)

    def reserve(
        self,
        account_id: int,
        facebook_user_id: int,
        phone_number_id: int,
        lookup_event_id: int | None,
        period_start: date,
        now: datetime,
    ) -> RevealDecision:
        key = (account_id, period_start, facebook_user_id)
        if key in self.reveals:
            return RevealDecision(True, False, self.reveals[key], self.used, self.limit)
        if self.used >= self.limit:
            return RevealDecision(False, False, None, self.used, self.limit)
        self.used += 1
        reveal_id = len(self.reveals) + 1
        self.reveals[key] = reveal_id
        return RevealDecision(True, True, reveal_id, self.used, self.limit)

    def current_usage(
        self, account_id: int, period_start: date
    ) -> int:
        return self.used


class ScriptedCursor:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self.rows = list(rows)
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.commands.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Connection:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self):
        return self.cursor_value


def connect(cursor: ScriptedCursor):
    return lambda _url: Connection(cursor)


def test_same_user_in_same_month_is_not_charged_twice() -> None:
    repository = MemoryQuotaRepository()
    quota = ContactQuotaService(repository, ZoneInfo("Asia/Ho_Chi_Minh"))

    first = quota.reserve(7, 11, 21, None, NOW)
    second = quota.reserve(7, 11, 21, None, NOW)

    assert (first.charged, second.charged) == (True, False)
    assert second.used == first.used == 1


def test_upgrade_keeps_usage_and_raises_limit() -> None:
    repository = MemoryQuotaRepository(limit=100)
    repository.used = 100
    quota = ContactQuotaService(repository, ZoneInfo("Asia/Ho_Chi_Minh"))

    repository.limit = 1000
    decision = quota.precheck(7, 99, NOW)

    assert decision.used == 100
    assert decision.limit == 1000
    assert decision.allowed is True


def test_current_usage_uses_the_product_month() -> None:
    repository = MemoryQuotaRepository()
    repository.used = 2
    quota = ContactQuotaService(repository, ZoneInfo("Asia/Ho_Chi_Minh"))

    assert quota.current_usage(7, NOW) == 2


def test_postgres_reservation_locks_usage_and_increments_once() -> None:
    cursor = ScriptedCursor([(100,), (99,), None, (55,), (100,)])
    repository = PostgresContactQuotaRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    decision = repository.reserve(7, 11, 21, None, PERIOD, NOW)

    assert decision == RevealDecision(True, True, 55, 100, 100)
    assert any("FOR UPDATE" in sql for sql, _ in cursor.commands)
    assert sum("UPDATE usage_monthly" in sql for sql, _ in cursor.commands) == 1


def test_postgres_current_usage_reads_without_creating_or_incrementing() -> None:
    cursor = ScriptedCursor([(2,)])
    repository = PostgresContactQuotaRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    assert repository.current_usage(7, PERIOD) == 2
    sql = "\n".join(command for command, _params in cursor.commands)
    assert "SELECT used_contact_count" in sql
    assert "INSERT INTO usage_monthly" not in sql
    assert "UPDATE usage_monthly" not in sql


def test_existing_reveal_is_allowed_even_when_usage_is_at_limit() -> None:
    cursor = ScriptedCursor([(100,), (100,), (55,)])
    repository = PostgresContactQuotaRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    decision = repository.reserve(7, 11, 21, None, PERIOD, NOW)

    assert decision == RevealDecision(True, False, 55, 100, 100)
    assert not any("INSERT INTO account_contact_reveals" in sql for sql, _ in cursor.commands)


def test_new_reveal_is_denied_without_insert_when_usage_is_at_limit() -> None:
    cursor = ScriptedCursor([(100,), (100,), None])
    repository = PostgresContactQuotaRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    decision = repository.reserve(7, 12, 22, None, PERIOD, NOW)

    assert decision == RevealDecision(False, False, None, 100, 100)
    assert not any("INSERT INTO account_contact_reveals" in sql for sql, _ in cursor.commands)
