from contextlib import contextmanager
from dataclasses import asdict, replace
from uuid import uuid4

import pytest

from fb_crawl.interaction_sessions.models import SessionError
from fb_crawl.interaction_sessions.postgres import PostgresInteractionSessionRepository, validate_batch
from tests.unit.interaction_sessions.test_models import NOW, row

def test_friend_rows_only_enter_friend_sessions():
    item = row(kind='friend', text='')
    assert validate_batch('friends', (item,), {}, 0) == (item,)
    for kind in ('comments', 'reactions'):
        with pytest.raises(SessionError, match='session_kind_conflict'):
            validate_batch(kind, (item,), {}, 0)
    with pytest.raises(SessionError, match='session_kind_conflict'):
        validate_batch('friends', (row(),), {}, 0)


def test_retry_does_not_count_existing_rows_again_at_cap():
    item = row()
    existing = {item.client_row_id: item}
    assert validate_batch("comments", (item,), existing, 5000) == ()
    changed = replace(item, text="edited", row_revision=2)
    assert validate_batch("comments", (changed,), existing, 5000) == (changed,)


def test_cap_and_duplicate_ids_fail_before_any_write():
    item = row()
    with pytest.raises(SessionError, match="session_limit_reached"):
        validate_batch("comments", (item,), {}, 5000)
    with pytest.raises(SessionError, match="session_duplicate_row"):
        validate_batch("comments", (item, item), {}, 0)


def test_rejects_changed_identity_kind_or_original_id_even_with_new_revision():
    item = row()
    for changes in ({"kind": "reply"}, {"identity": replace(item.identity, facebook_uid="100999")}, {"interaction_id": "other"}):
        with pytest.raises(SessionError, match="session_row_conflict"):
            validate_batch("comments", (replace(item, row_revision=2, **changes),), {item.client_row_id: item}, 1)


def test_reaction_rows_cannot_enter_comment_session_and_new_text_needs_revision():
    item = row()
    with pytest.raises(SessionError):
        validate_batch("reactions", (item,), {}, 0)
    with pytest.raises(SessionError, match="session_row_conflict"):
        validate_batch("comments", (replace(item, text="edited"),), {item.client_row_id: item}, 1)
    assert validate_batch("comments", (item,), {item.client_row_id: replace(item, row_revision=2, text="new")}, 1) == ()


class Cursor:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
    def fetchone(self):
        return next(self.responses)


class Repository(PostgresInteractionSessionRepository):
    def __init__(self, responses):
        self.cursor = Cursor(responses)
    @contextmanager
    def _connect(self):
        yield self.cursor


def test_foreign_session_batch_stops_at_account_scoped_lock():
    repo = Repository([None])
    identifier = uuid4()
    with pytest.raises(SessionError) as error:
        repo.upsert_rows(8, identifier, (row(),), NOW)
    assert error.value.status == 404
    sql, params = repo.cursor.calls[0]
    assert "account_id = %s" in sql and "FOR UPDATE" in sql
    assert params == (identifier, 8)
    assert len(repo.cursor.calls) == 1


def test_stale_resume_cannot_reopen_stopped_session():
    repo = Repository([{"id": uuid4(), "status": "stopped", "revision": 3}])
    with pytest.raises(SessionError, match="session_revision_conflict"):
        repo.transition(7, uuid4(), "running", 1, NOW)
    assert len(repo.cursor.calls) == 1
