from dataclasses import asdict

from fb_crawl.contacts.models import LookupScanContext, LookupScanMode, LookupSourceType
from fb_crawl.contacts.service import ContactLookupRequest, ContactLookupService
from .models import SessionError


class SessionLookupService:
    def __init__(self, repository, contacts: ContactLookupService | None):
        self.repository = repository
        self.contacts = contacts

    def lookup(self, account, device, session_id, person_id, now, retry_failed=False, resolved_uid=None):
        if self.contacts is None:
            raise SessionError("provider_not_configured", 503)
        person = self.repository.get_person(account.id, session_id, person_id)
        session = self.repository.get(account.id, session_id)
        if session is None:
            raise SessionError("interaction_session_not_found", 404)
        if resolved_uid is not None and person.facebook_uid and person.facebook_uid != resolved_uid:
            raise SessionError("interaction_person_uid_conflict", 409)
        identity = asdict(person)
        if resolved_uid is not None:
            identity["facebook_uid"] = resolved_uid
        result = self.contacts.lookup(
            account, device, ContactLookupRequest(**identity), now,
            session_person_id=person_id, retry_failed=retry_failed,
            scan_context=LookupScanContext(
                mode=LookupScanMode.MANUAL_LOADED,
                source_type=LookupSourceType.COMMENT_AUTHOR if session.kind == "comments" else LookupSourceType.PROFILE,
                source_url=session.source_url,
            ),
        )
        self.repository.record_result(account.id, session_id, person_id, result.event_id, str(result.state), now)
        return result
