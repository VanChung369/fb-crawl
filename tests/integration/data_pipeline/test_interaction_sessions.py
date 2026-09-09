"""Run only against an explicitly configured disposable PostgreSQL database."""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from fb_crawl.interaction_sessions.models import RowFilters, SessionCreate, SessionError, SessionFilters
from fb_crawl.interaction_sessions.postgres import PostgresInteractionSessionRepository
from fb_crawl.contacts.postgres import PostgresContactRepository
from fb_crawl.contacts.models import LookupScanContext
from fb_data_pipeline.core.models import FacebookIdentity
from fb_data_pipeline.repositories.migrations import MigrationRunner
from tests.integration.data_pipeline.test_history_isolation import _safe_test_database
from tests.unit.interaction_sessions.test_models import NOW, row

URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not _safe_test_database(URL), reason="Requires explicit disposable PostgreSQL TEST_DATABASE_URL ending in _test")


@pytest.fixture
def store():
    MigrationRunner(URL).apply()
    ids = []
    with psycopg.connect(URL) as connection:
        for _ in range(2):
            email = f"session-{uuid4()}@example.test"
            ids.append(connection.execute("""INSERT INTO accounts(normalized_email,display_email,password_hash,status,email_verified_at)
                VALUES (%s,%s,'test-only','active',%s) RETURNING id""", (email,email,NOW)).fetchone()[0])
    yield PostgresInteractionSessionRepository(URL), ids[0], ids[1]
    with psycopg.connect(URL) as connection:
        connection.execute("DELETE FROM accounts WHERE id=ANY(%s)", (ids,))


def test_retry_edits_ownership_pagination_and_delete(store):
    repo, owner, other = store
    value = SessionCreate(uuid4(), "https://www.facebook.com/sample.user/posts/123456", "comments")
    session = repo.create(owner, value, NOW)
    assert repo.create(owner, value, NOW).id == session.id
    with pytest.raises(SessionError):
        repo.create(owner, replace(value, kind="reactions"), NOW)
    first, second = row(), row(interaction_id="comment-2")
    repo.upsert_rows(owner, session.id, (first, second), NOW)
    repo.upsert_rows(owner, session.id, (first, second), NOW)
    summary = repo.get(owner, session.id)
    assert (summary.counters.interactions, summary.counters.people) == (2, 1)
    repo.upsert_rows(owner, session.id, (replace(first, text="edited", row_revision=2),), NOW)
    assert repo.list_rows(owner, session.id, RowFilters(text="edited")).items[0].text == "edited"
    page = repo.list_rows(owner, session.id, RowFilters(), limit=1)
    next_page = repo.list_rows(owner, session.id, RowFilters(), page.next_cursor, 1)
    assert page.items[0].client_row_id != next_page.items[0].client_row_id
    assert next_page.next_cursor is None
    assert repo.get(other, session.id) is None
    assert not repo.list_sessions(other, SessionFilters()).items
    for action in (
        lambda: repo.upsert_rows(other, session.id, (first,), NOW),
        lambda: repo.transition(other, session.id, "stopped", 1, NOW),
        lambda: repo.list_rows(other, session.id, RowFilters()),
        lambda: repo.delete(other, session.id),
    ):
        with pytest.raises(SessionError) as error:
            action()
        assert error.value.status == 404
    repo.delete(owner, session.id)
    with pytest.raises(SessionError):
        repo.upsert_rows(owner, session.id, (first,), NOW)


def test_concurrent_batches_enforce_cap_atomically(store):
    repo, owner, _ = store
    session = repo.create(owner, SessionCreate(uuid4(), "https://www.facebook.com/sample.user/posts/123456", "comments"), NOW)
    for batch in range(50):
        size = 99 if batch == 49 else 100
        repo.upsert_rows(owner, session.id, tuple(row(interaction_id=f"{batch}-{i}") for i in range(size)), NOW)
    def upload():
        try:
            repo.upsert_rows(owner, session.id, (row(),), NOW)
            return "saved"
        except SessionError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload(), range(2)))
    assert sorted(results) == ["saved", "session_limit_reached"]
    assert repo.get(owner, session.id).counters.interactions == 5000


def test_concurrent_person_requests_bind_one_event_and_recover_interrupted_attempt(store):
    repo, owner, other = store
    session = repo.create(owner, SessionCreate(uuid4(), "https://www.facebook.com/sample.user/posts/123456", "comments"), NOW)
    ack = repo.upsert_rows(owner, session.id, (row(), row(interaction_id="second")), NOW)
    person_id = ack.accepted[0].person_id
    contacts = PostgresContactRepository(URL)
    requested = FacebookIdentity(uid="100123", username="sample.user")
    contact = contacts.resolve_identity(requested)
    def claim(at=NOW, retry=False):
        return contacts.claim_session_event(owner, None, contact, requested, at, LookupScanContext(), person_id, retry, timedelta(seconds=90))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert results[0][0].id == results[1][0].id
    assert sorted(value[1] for value in results) == [False, True]
    with pytest.raises(SessionError):
        contacts.claim_session_event(other, None, contact, requested, NOW, LookupScanContext(), person_id, False, timedelta(seconds=90))
    recovered, owned = claim(NOW + timedelta(seconds=91))
    assert not owned
    assert recovered.outcome == "failed"
    assert recovered.safe_error_code == "contact_lookup_interrupted"
    retried, owned = claim(NOW + timedelta(seconds=92), True)
    assert owned and retried.id != recovered.id
    repo.delete(owner, session.id)
    repo.record_result(owner, session.id, person_id, retried.id, "failed", NOW)
    assert repo.get(owner, session.id) is None
    assert contacts.get_lookup_event(owner, retried.id) is not None
