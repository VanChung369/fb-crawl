from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from fb_data_pipeline.repositories.errors import DatabaseError


@dataclass(frozen=True, slots=True)
class ProductMetrics:
    accounts_total: int
    accounts_active: int
    subscriptions_valid: int
    devices_active: int
    license_keys_total: int
    license_keys_available: int
    lookups_total: int
    lookups_found: int
    lookups_not_found: int
    lookups_failed: int
    lookups_processing: int
    quota_rejections: int
    cache_hits: int
    negative_cache_hits: int
    provider_calls: int
    provider_found: int
    provider_not_found: int
    provider_failed: int
    provider_latency_average_ms: int
    unique_contact_reveals: int
    exports_total: int
    exports_queued: int
    exports_running: int
    exports_completed: int
    exports_failed: int
    exports_expired: int


class ProductMetricsRepository(Protocol):
    def get(self) -> ProductMetrics: ...


class PostgresProductMetricsRepository:
    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(
            1, round(statement_timeout_seconds * 1000)
        )
        self.connect_factory = connect_factory

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{self.statement_timeout_ms}ms",),
                    )
                    yield cursor
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

    def get(self) -> ProductMetrics:
        with self._connect() as cursor:
            cursor.execute(
                """
                SELECT
                  (SELECT count(*) FROM accounts),
                  (SELECT count(*) FROM accounts WHERE status = 'active'),
                  (SELECT count(*) FROM account_subscriptions
                   WHERE status = 'valid' AND starts_at <= now() AND now() < ends_at),
                  (SELECT count(*) FROM devices WHERE status = 'active'),
                  (SELECT count(*) FROM license_keys),
                  (SELECT count(*) FROM license_keys WHERE status = 'available'),
                  (SELECT count(*) FROM lookup_events),
                  (SELECT count(*) FROM lookup_events WHERE outcome = 'found'),
                  (SELECT count(*) FROM lookup_events WHERE outcome = 'not_found'),
                  (SELECT count(*) FROM lookup_events WHERE outcome = 'failed'),
                  (SELECT count(*) FROM lookup_events WHERE outcome = 'processing'),
                  (SELECT count(*) FROM lookup_events
                   WHERE outcome = 'quota_exceeded'),
                  (SELECT count(*) FROM lookup_events WHERE result_source = 'cache'),
                  (SELECT count(*) FROM lookup_events
                   WHERE result_source = 'negative_cache'),
                  (SELECT count(*) FROM lookup_events WHERE provider_called),
                  (SELECT count(*) FROM lookup_events
                   WHERE provider_called AND outcome = 'found'),
                  (SELECT count(*) FROM lookup_events
                   WHERE provider_called AND outcome = 'not_found'),
                  (SELECT count(*) FROM lookup_events
                   WHERE provider_called AND outcome = 'failed'),
                  (SELECT COALESCE(ROUND(AVG(
                     EXTRACT(EPOCH FROM (completed_at - created_at)) * 1000
                   )), 0)::bigint FROM lookup_events
                   WHERE provider_called AND completed_at IS NOT NULL),
                  (SELECT count(*) FROM account_contact_reveals),
                  (SELECT count(*) FROM export_jobs),
                  (SELECT count(*) FROM export_jobs WHERE status = 'queued'),
                  (SELECT count(*) FROM export_jobs WHERE status = 'running'),
                  (SELECT count(*) FROM export_jobs WHERE status = 'completed'),
                  (SELECT count(*) FROM export_jobs WHERE status = 'failed'),
                  (SELECT count(*) FROM export_jobs WHERE status = 'expired')
                """
            )
            row = cursor.fetchone()
        if row is None or len(row) != 26:
            raise DatabaseError("Database product metrics read failed.")
        return ProductMetrics(*(int(value or 0) for value in row))
