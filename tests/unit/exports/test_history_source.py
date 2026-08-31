from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import Page
from fb_crawl.core.jobs import KeysetCursor, encode_cursor
from fb_crawl.exports.service import (
    HistoryExportSource,
    normalize_filter_snapshot,
)


NOW = datetime(2026, 8, 31, 3, tzinfo=UTC)


class History:
    def __init__(self) -> None:
        self.queries = []

    def list(self, query):
        self.queries.append(query)
        if query.cursor is None:
            return Page(
                ("first",), encode_cursor(KeysetCursor(NOW, 71))
            )
        return Page(("second",), None)


def test_filter_snapshot_is_normalized_and_json_safe() -> None:
    snapshot = normalize_filter_snapshot(
        {
            "outcome": "found",
            "phone": "0981234567",
            "created_from": NOW,
            "name": None,
        }
    )

    assert snapshot == {
        "outcome": "found",
        "phone": "+84981234567",
        "created_from": "2026-08-31T03:00:00Z",
    }


@pytest.mark.parametrize("field", ["account_id", "cursor", "limit", "sql"])
def test_filter_snapshot_rejects_client_controlled_scope_or_pagination(field) -> None:
    with pytest.raises(ValidationError):
        normalize_filter_snapshot({field: 7})


def test_history_export_source_paginates_through_quota_safe_history_service() -> None:
    history = History()
    source = HistoryExportSource(history)

    assert list(source.iter_export(7, {"outcome": "found"})) == [
        "first",
        "second",
    ]
    assert [query.account_id for query in history.queries] == [7, 7]
    assert history.queries[0].cursor is None
    assert history.queries[1].cursor is not None
    assert all(query.limit == 100 for query in history.queries)
