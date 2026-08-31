from __future__ import annotations

from fb_crawl.exports.metrics import PostgresProductMetricsRepository


class Cursor:
    def __init__(self, row):
        self.row = row
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.commands.append((sql, params))

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, cursor):
        self.value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self.value


def test_product_metrics_are_aggregate_only_and_cover_operational_counters() -> None:
    cursor = Cursor(tuple(range(1, 27)))
    repository = PostgresProductMetricsRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: Connection(cursor),
    )

    metrics = repository.get()

    assert metrics.accounts_total == 1
    assert metrics.provider_latency_average_ms == 19
    assert metrics.exports_expired == 26
    sql = next(sql for sql, _params in cursor.commands if "lookup_events" in sql)
    for required in (
        "accounts",
        "account_subscriptions",
        "devices",
        "license_keys",
        "lookup_events",
        "account_contact_reveals",
        "export_jobs",
    ):
        assert required in sql
    assert "normalized_phone" not in sql
    assert "display_phone" not in sql
    assert "facebook_uid" not in sql
