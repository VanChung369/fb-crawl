from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fb_crawl.history.models import AccountHistoryQuery
from fb_crawl.history.postgres import PostgresHistoryRepository


NOW = datetime(2026, 8, 31, 3, tzinfo=UTC)


class Cursor:
    def __init__(self, rows=()) -> None:
        self.rows = list(rows)
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None) -> None:
        self.commands.append((sql, params))
        if "DELETE FROM lookup_events" in sql:
            self.rowcount = 1

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self.value


def row(account_id: int = 5):
    return (
        71,
        account_id,
        7,
        41,
        "100123",
        "sample.user",
        "Sample User",
        "https://www.facebook.com/sample.user",
        81,
        "+84981234567",
        "found",
        "cache",
        False,
        True,
        "",
        NOW,
        NOW + timedelta(seconds=1),
    )


def test_history_list_binds_account_before_every_filter() -> None:
    cursor = Cursor([row()])
    repository = PostgresHistoryRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: Connection(cursor),
    )

    page = repository.list(
        AccountHistoryQuery(
            account_id=5,
            uid="100",
            outcome="found",
            limit=20,
        )
    )

    assert [item.account_id for item in page.items] == [5]
    query = next(
        (sql, params)
        for sql, params in cursor.commands
        if "FROM lookup_events AS events" in sql
    )
    assert "events.account_id = %s" in query[0]
    assert query[1][0] == 5
    assert query[1][-1] == 21


def test_history_get_and_delete_are_tenant_scoped() -> None:
    cursor = Cursor([row()])
    repository = PostgresHistoryRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: Connection(cursor),
    )

    item = repository.get(5, 71)
    deleted = repository.delete_one(5, 71)

    assert item is not None and item.account_id == 5
    assert deleted is True
    scoped = [
        (sql, params)
        for sql, params in cursor.commands
        if "lookup_events" in sql and "set_config" not in sql
    ]
    assert all(params[:2] == (5, 71) for _sql, params in scoped)


def test_filtered_delete_never_touches_reveal_or_usage_ledgers() -> None:
    cursor = Cursor()
    repository = PostgresHistoryRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: Connection(cursor),
    )

    count = repository.delete_filtered(
        AccountHistoryQuery(account_id=5, outcome="failed")
    )

    assert count == 1
    sql = "\n".join(command for command, _params in cursor.commands)
    assert "DELETE FROM lookup_events" in sql
    assert "account_contact_reveals" not in sql
    assert "usage_monthly" not in sql


def test_filtered_delete_joins_identity_and_phone_filter_tables() -> None:
    cursor = Cursor()
    repository = PostgresHistoryRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: Connection(cursor),
    )

    repository.delete_filtered(
        AccountHistoryQuery(
            account_id=5,
            name="Sample",
            uid="100",
            username="sample",
            phone="0981234567",
        )
    )

    delete_sql, params = next(
        (sql, values)
        for sql, values in cursor.commands
        if "DELETE FROM lookup_events" in sql
    )
    assert "USING facebook_users AS users, phone_numbers AS numbers" in delete_sql
    assert "users.id = events.facebook_user_id" in delete_sql
    assert "numbers.id = events.revealed_phone_number_id" in delete_sql
    assert params is not None and params[0] == 5
