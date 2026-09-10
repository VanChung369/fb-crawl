import pytest

from fb_crawl.history.leads import match_lead, lead_value
from fb_crawl.interaction_sessions.models import SessionIdentity, SessionError


def test_username_only_customer_matches_when_uid_becomes_available():
    row = {'facebook_uid': None, 'username': 'sample', 'status': 'called', 'notes': 'Đã gọi', 'revision': 3}
    assert lead_value(match_lead([row], SessionIdentity(facebook_uid='100123', username='SAMPLE'))) == {
        'status': 'called', 'notes': 'Đã gọi', 'revision': 3,
    }
    assert lead_value(match_lead([row], SessionIdentity(username='other'))) == {
        'status': 'unprocessed', 'notes': '', 'revision': 0,
    }


def test_conflicting_uids_or_separate_alias_records_are_not_silently_merged():
    rows = [{'facebook_uid': '100123', 'username': 'sample'}]
    with pytest.raises(SessionError, match='lead_identity_conflict'):
        match_lead(rows, SessionIdentity(facebook_uid='999999', username='sample'))
    rows.append({'facebook_uid': None, 'username': 'other'})
    with pytest.raises(SessionError):
        match_lead(rows, SessionIdentity(facebook_uid='100123', username='other'))
