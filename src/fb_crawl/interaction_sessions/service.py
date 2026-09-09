from dataclasses import asdict

from fb_crawl.core.jobs import Page
from fb_crawl.history.service import HistoryService
from .models import SessionError
from .repository import InteractionSessionRepository


class InteractionSessionService:
    def __init__(self, repository: InteractionSessionRepository, history: HistoryService):
        self.repository = repository
        self.history = history

    def create(self, account_id, value, now):
        return self.repository.create(account_id, value, now)

    def get(self, account_id, session_id):
        result = self.repository.get(account_id, session_id)
        if result is None:
            raise SessionError("interaction_session_not_found", 404)
        return result

    def upsert_rows(self, account_id, session_id, rows, now):
        return self.repository.upsert_rows(account_id, session_id, rows, now)

    def transition(self, account_id, session_id, status, revision, now):
        return self.repository.transition(account_id, session_id, status, revision, now)

    def delete(self, account_id, session_id):
        return self.repository.delete(account_id, session_id)

    def list_sessions(self, account_id, filters, cursor, limit):
        return self.repository.list_sessions(account_id, filters, cursor, limit)

    def list_rows(self, account_id, session_id, filters, cursor, limit):
        page = self.repository.list_rows(account_id, session_id, filters, cursor, limit)
        contacts = {}
        result = []
        for row in page.items:
            value = asdict(row)
            event_id = value.pop("lookup_event_id")
            if event_id is not None and event_id not in contacts:
                item = self.history.get(account_id, event_id)
                contacts[event_id] = self._contact(account_id, item) if item else None
            value["contact"] = contacts.get(event_id)
            result.append(value)
        return Page(tuple(result), page.next_cursor)

    def _contact(self, account_id, item):
        # HistoryService has already applied ownership/deletion/reveal rules.
        quota = self.history.quota.precheck(account_id, item.facebook_user_id, self.history.clock())
        state = str(item.outcome)
        safe_error = item.safe_error_code
        if state == "found" and not item.phone:
            state = "quota_exceeded" if not quota.allowed else "failed"
            safe_error = "contact_quota_exhausted" if not quota.allowed else "contact_result_unavailable"
        return {
            "user": {"facebook_uid": item.facebook_uid, "username": item.username,
                     "name": item.name, "profile_url": item.profile_url},
            "contact": {"phone": item.phone},
            "meta": {"event_id": item.id, "state": state, "source": str(item.source),
                     "observed_at": None, "provider_called": item.provider_called,
                     "quota_charged": False, "monthly_used": quota.used, "monthly_limit": quota.limit,
                     "poll_url": None, "safe_error_code": safe_error},
        }
