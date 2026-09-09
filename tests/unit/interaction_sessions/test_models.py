from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.interaction_sessions.models import (
    SessionCreate, SessionIdentity, SessionRowInput, RowFilters,
    SessionFilters, decode_page_cursor, encode_page_cursor,
)

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def row(**changes):
    value = SessionRowInput(
        client_row_id=uuid4(), row_revision=1, interaction_id="comment-1",
        synthetic=True, parent_id="", kind="comment",
        identity=SessionIdentity("100123", "sample.user", "Sample", "https://www.facebook.com/sample.user"),
        text="A comment", observed_at=NOW,
    )
    return replace(value, **changes)


@pytest.mark.parametrize("url", ["https://evil.test/posts/123", "https://www.facebook.com/", "https://user:pass@www.facebook.com/posts/123", "https://www.facebook.com/groups/123/members"])
def test_rejects_non_post_sources(url):
    with pytest.raises(ValidationError):
        SessionCreate(uuid4(), url, "comments")


def test_normalizes_source_and_identity_without_losing_string_uid():
    value = SessionCreate(uuid4(), "https://www.facebook.com/sample.user/posts/123456?locale=vi_VN", "comments")
    assert value.source_url == "https://www.facebook.com/sample.user/posts/123456"
    assert SessionIdentity("100123", "Sample.User", "Sample", "").key == "uid:100123"
    assert SessionIdentity("", "Sample.User", "Sample", "").key == "username:sample.user"


@pytest.mark.parametrize("changes", [
    {"client_row_id": "bad"}, {"row_revision": 0}, {"row_revision": True},
    {"kind": "like"}, {"text": "x" * 10001}, {"observed_at": NOW.replace(tzinfo=None)},
    {"interaction_id": ""}, {"synthetic": "false"},
])
def test_rejects_invalid_rows(changes):
    with pytest.raises(ValidationError):
        row(**changes)


def test_rejects_missing_or_conflicting_identity():
    for uid, username, url in [("", "", ""), ("bad", "", ""), ("100123", "", "https://www.facebook.com/profile.php?id=999999")]:
        with pytest.raises(ValidationError):
            SessionIdentity(uid, username, "", url)


def test_cursor_is_bound_to_account_session_and_filters():
    filters = RowFilters(text="one")
    token = encode_page_cursor(NOW, uuid4(), "account-1/session-1", filters)
    assert decode_page_cursor(token, "account-1/session-1", filters)[0] == NOW
    for scope, query in [("account-2/session-1", filters), ("account-1/session-1", RowFilters(text="two"))]:
        with pytest.raises(ValidationError):
            decode_page_cursor(token, scope, query)
    with pytest.raises(ValidationError):
        decode_page_cursor("bad", "account-1/session-1", filters)


def test_filter_validation():
    with pytest.raises(ValidationError):
        RowFilters(outcome="invented")
    with pytest.raises(ValidationError):
        SessionFilters(created_from=NOW, created_to=NOW.replace(year=2025))
