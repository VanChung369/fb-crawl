from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

from fb_crawl.core.jobs import Page
from fb_crawl.entitlements.quota import QuotaPrecheck, RevealDecision
from fb_crawl.history.service import HistoryService
from fb_crawl.interaction_sessions.models import RowFilters, SessionRow
from fb_crawl.interaction_sessions.service import InteractionSessionService
from tests.unit.api.test_history_routes import HistoryRepositoryFake
from tests.unit.interaction_sessions.test_models import NOW, row


class Quota:
    allowed = True
    def reserve(self, *args):
        return RevealDecision(self.allowed, False, 91, 42, 100)
    def precheck(self, *args):
        return QuotaPrecheck(self.allowed, False, 42, 100)


def test_rows_preserve_individual_comments_but_project_only_authorized_history_phone():
    history = HistoryRepositoryFake()
    quota = Quota()
    first, second = row(), row(interaction_id="second")
    person = uuid4()
    values = tuple(SessionRow(**{**asdict(r), "identity": r.identity, "lookup_event_id": 71}, person_id=person) for r in (first, second))
    repo = SimpleNamespace(list_rows=lambda *args: Page(values, None))
    service = InteractionSessionService(repo, HistoryService(history, quota, clock=lambda: NOW))
    rows = service.list_rows(7, uuid4(), RowFilters(), None, 100).items
    assert len(rows) == 2
    assert rows[0]["contact"]["contact"]["phone"] == "+84981234567"
    assert rows[0]["contact"]["meta"]["monthly_used"] == 42
    assert "lookup_event_id" not in rows[0]
    quota.allowed = False
    rows = service.list_rows(7, uuid4(), RowFilters(), None, 100).items
    assert rows[0]["contact"]["contact"]["phone"] == ""
    assert rows[0]["contact"]["meta"]["state"] == "quota_exceeded"
    history.visible = False
    rows = service.list_rows(7, uuid4(), RowFilters(), None, 100).items
    assert rows[0]["contact"] is None
    assert rows[0]["text"] == "A comment"


def test_saving_attaches_existing_owned_lookup_without_revealing_or_looking_up_phone():
    from unittest.mock import Mock
    from fb_crawl.interaction_sessions.models import BatchAck, AcceptedRow
    history = HistoryRepositoryFake()
    quota = Mock()
    item = row(lookup_event_id=71)
    person, session = uuid4(), uuid4()
    repo = SimpleNamespace(upsert_rows=Mock(return_value=BatchAck(2, (AcceptedRow(item.client_row_id, 1, person),))), attach_result=Mock(return_value=3))
    service = InteractionSessionService(repo, HistoryService(history, quota))
    service.upsert_rows(7, session, (item,), NOW)
    repo.attach_result.assert_called_once_with(7, session, person, 71, NOW)
    quota.reserve.assert_not_called()


def test_saving_rejects_foreign_or_mismatched_lookup_events_before_upload():
    import pytest
    from unittest.mock import Mock
    from fb_crawl.interaction_sessions.models import SessionError, SessionIdentity
    history = HistoryRepositoryFake()
    repo = SimpleNamespace(upsert_rows=Mock())
    service = InteractionSessionService(repo, HistoryService(history, Quota()))
    for item in (row(lookup_event_id=72), row(lookup_event_id=71, identity=SessionIdentity("999999"))):
        with pytest.raises(SessionError, match="session_lookup_event_conflict"):
            service.upsert_rows(7, uuid4(), (item,), NOW)
    repo.upsert_rows.assert_not_called()


def test_save_reconciliation_reads_owned_rows_without_revealing_phone_or_reserving_quota():
    from unittest.mock import Mock
    item = row()
    value = SessionRow(**{**asdict(item), "identity": item.identity, "lookup_event_id": 71}, person_id=uuid4())
    history = Mock()
    repo = SimpleNamespace(list_rows=lambda *args: Page((value,), None))
    service = InteractionSessionService(repo, history)
    result = service.list_rows(7, uuid4(), RowFilters(), None, 100, include_contact=False).items[0]
    assert result["contact"] is None and result["lookup_event_id"] == 71
    history.get.assert_not_called()
    history.quota.reserve.assert_not_called()
