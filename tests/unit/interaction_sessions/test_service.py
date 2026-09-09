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
    values = tuple(SessionRow(**{**asdict(r), "identity": r.identity}, person_id=person, lookup_event_id=71) for r in (first, second))
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
