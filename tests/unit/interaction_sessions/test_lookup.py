from dataclasses import replace
from uuid import uuid4

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
