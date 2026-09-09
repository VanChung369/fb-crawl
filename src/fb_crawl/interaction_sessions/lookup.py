from dataclasses import asdict

from fb_crawl.contacts.service import ContactLookupRequest, ContactLookupService
from .models import SessionError, SessionIdentity


class SessionLookupService:
    def __init__(self, repository, contacts: ContactLookupService | None):
        self.repository = repository
        self.contacts = contacts

    def lookup(self, account, device, session_id, person_id, now, retry_failed=False):
        if self.contacts is None:
            raise SessionError("provider_not_configured", 503)
        person = self.repository.get_person(account.id, session_id, person_id)
        result = self.contacts.lookup(
            account, device, ContactLookupRequest(**asdict(person)), now,
            session_person_id=person_id, retry_failed=retry_failed,
        )
        self.repository.record_result(account.id, session_id, person_id, result.event_id, str(result.state), now)
        return result
