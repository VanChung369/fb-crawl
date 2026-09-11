from dataclasses import replace
from uuid import uuid4
from types import SimpleNamespace

import pytest

from fb_crawl.contacts.models import CachedContact, LookupOutcome
from tests.unit.contacts.test_service import ACCOUNT, DEVICE, REQUEST, NOW, CONTACT, event, service


def test_existing_processing_session_attempt_never_starts_new_provider_work():
    lookup, contacts, quota, pipeline = service(CachedContact(CONTACT.id, None, "", None, None))
    person_id = uuid4()
    contacts.claim_session_event = lambda *args: (event(), False)
    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW, session_person_id=person_id)
    assert result.state == LookupOutcome.PROCESSING
    assert result.event_id == 71
    assert not pipeline.calls
    assert not quota.reserve_calls
    assert not contacts.create_calls


def test_existing_failed_session_attempt_is_returned_without_implicit_retry():
    lookup, contacts, _, pipeline = service(CachedContact(CONTACT.id, None, "", None, None))
    contacts.created = replace(event(), outcome=LookupOutcome.FAILED, completed_at=NOW, safe_error_code="provider_authentication_failed")
    contacts.claim_session_event = lambda *args: (contacts.created, False)
    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW, session_person_id=uuid4())
    assert result.state == LookupOutcome.FAILED
    assert result.safe_error_code == "provider_authentication_failed"
    assert not pipeline.calls


def test_session_lookup_records_collection_source_in_contact_history():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from fb_crawl.interaction_sessions.lookup import SessionLookupService
    from fb_crawl.interaction_sessions.models import SessionIdentity
    from fb_crawl.contacts.service import ContactLookupResult
    from fb_crawl.contacts.models import LookupSource
    repository = Mock()
    repository.get_person.return_value = SessionIdentity(facebook_uid="100123", username="sample.user")
    repository.get.return_value = SimpleNamespace(source_url="https://www.facebook.com/groups/123/posts/456", kind="comments")
    contacts = Mock()
    contacts.lookup.return_value = ContactLookupResult(event_id=71, state=LookupOutcome.NOT_FOUND, source=LookupSource.PROVIDER, user=CONTACT.identity)
    session_id, person_id = uuid4(), uuid4()
    SessionLookupService(repository, contacts).lookup(ACCOUNT, DEVICE, session_id, person_id, NOW)
    context = contacts.lookup.call_args.kwargs["scan_context"]
    assert context.mode == "manual_loaded"
    assert context.source_type == "comment_author"
    assert context.source_url == repository.get.return_value.source_url
    repository.record_result.assert_called_once_with(ACCOUNT.id, session_id, person_id, 71, "not_found", NOW)


@pytest.mark.parametrize("stored_uid", ["", "100123"])
def test_session_lookup_uses_resolved_uid_with_owned_person_identity(stored_uid):
    from fb_crawl.interaction_sessions.lookup import SessionLookupService
    from fb_crawl.interaction_sessions.models import SessionIdentity
    identity = SessionIdentity(facebook_uid=stored_uid, username="sample.user", name="Sample User", profile_url="https://www.facebook.com/sample.user")
    requests = []
    class Repository:
        def get_person(self, account_id, session_id, person_id):
            assert account_id == ACCOUNT.id
            return identity
        def get(self, account_id, session_id):
            return SimpleNamespace(kind="comments", source_url="https://www.facebook.com/sample.user/posts/123456")
        def record_result(self, *args):
            pass
    class Contacts:
        def lookup(self, account, device, request, now, **kwargs):
            requests.append(request)
            return SimpleNamespace(event_id=71, state=LookupOutcome.NOT_FOUND)
    SessionLookupService(Repository(), Contacts()).lookup(ACCOUNT, DEVICE, uuid4(), uuid4(), NOW, resolved_uid="100123")
    assert requests[0].facebook_uid == "100123"
    assert requests[0].username == identity.username
    assert requests[0].name == identity.name
    assert requests[0].profile_url == identity.profile_url


def test_session_lookup_rejects_resolved_uid_conflicting_with_owned_person():
    from unittest.mock import Mock
    from fb_crawl.interaction_sessions.lookup import SessionLookupService
    from fb_crawl.interaction_sessions.models import SessionIdentity, SessionError
    repository, contacts = Mock(), Mock()
    repository.get_person.return_value = SessionIdentity(facebook_uid="100456")
    with pytest.raises(SessionError) as error:
        SessionLookupService(repository, contacts).lookup(ACCOUNT, DEVICE, uuid4(), uuid4(), NOW, resolved_uid="100123")
    assert error.value.code == "interaction_person_uid_conflict"
    assert error.value.status == 409
    contacts.lookup.assert_not_called()
