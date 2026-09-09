from __future__ import annotations

from datetime import timedelta

from fb_crawl.contacts.models import LookupOutcome, LookupSource
from fb_crawl.contacts.service import ContactLookupResult
from fb_crawl.auth.rate_limit import RateLimitPolicy
from fb_crawl.api.correlation import current_request_id
from fb_data_pipeline.core.models import FacebookIdentity
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_product_auth_routes import product_client
from tests.unit.api.test_product_auth_routes import WEB_ORIGIN


REQUEST = {
    "facebook_uid": "100123",
    "username": "sample.user",
    "profile_url": "https://www.facebook.com/sample.user",
}


class ContactLookupServiceFake:
    def __init__(self, result: ContactLookupResult) -> None:
        self.result = result
        self.lookup_calls: list[tuple[object, ...]] = []
        self.poll_accounts: list[int] = []
        self.request_ids: list[str] = []
        self.scan_contexts: list[object] = []
        self.event_visible = True

    def lookup(
        self,
        account,
        device,
        request,
        now,
        force_refresh: bool = False,
        scan_context=None,
    ) -> ContactLookupResult:
        self.lookup_calls.append(
            (account.id, device.id, request, now, force_refresh)
        )
        self.request_ids.append(current_request_id())
        self.scan_contexts.append(scan_context)
        return self.result

    def get_event(
        self, account_id: int, event_id: int
    ) -> ContactLookupResult | None:
        self.poll_accounts.append(account_id)
        if (
            not self.event_visible
            or account_id != 7
            or event_id != self.result.event_id
        ):
            return None
        return self.result


def result(
    outcome: LookupOutcome,
    *,
    error: str = "",
) -> ContactLookupResult:
    return ContactLookupResult(
        event_id=71,
        state=outcome,
        source=(
            LookupSource.CACHE
            if outcome is LookupOutcome.FOUND
            else LookupSource.NONE
        ),
        user=FacebookIdentity(
            uid="100123",
            username="sample.user",
            name="Sample User",
            profile_url="https://www.facebook.com/sample.user",
        ),
        phone=("+84981234567" if outcome is LookupOutcome.FOUND else ""),
        observed_at=(NOW - timedelta(minutes=1)),
        provider_called=False,
        quota_charged=outcome is LookupOutcome.FOUND,
        monthly_used=12,
        monthly_limit=100,
        safe_error_code=error,
    )


def headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_processing_lookup_returns_202_and_tenant_poll_url() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.PROCESSING))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    response = client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json=REQUEST,
    )

    assert response.status_code == 202
    assert response.json()["meta"]["state"] == "processing"
    assert response.json()["meta"]["poll_url"] == (
        "/api/v1/contacts/lookups/71"
    )


def test_found_lookup_returns_only_closed_contact_and_quota_fields() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    response = client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json={**REQUEST, "force_refresh": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "user": {
            "facebook_uid": "100123",
            "username": "sample.user",
            "name": "Sample User",
            "profile_url": "https://www.facebook.com/sample.user",
            "gender": "",
            "address": "",
            "birth_date": "",
        },
        "contact": {"phone": "+84981234567"},
        "meta": {
            "event_id": 71,
            "state": "found",
            "source": "cache",
            "observed_at": "2026-08-30T07:59:00Z",
            "provider_called": False,
            "quota_charged": True,
            "monthly_used": 12,
            "monthly_limit": 100,
            "poll_url": None,
            "safe_error_code": "",
        },
    }
    assert service.lookup_calls[0][-1] is True


def test_quota_and_identity_conflict_have_safe_statuses_without_phone() -> None:
    quota_service = ContactLookupServiceFake(
        result(
            LookupOutcome.QUOTA_EXCEEDED,
            error="contact_quota_exhausted",
        )
    )
    quota_client, _repository, _auth, access = product_client(
        contact_lookup_service=quota_service
    )
    quota_response = quota_client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json=REQUEST,
    )

    conflict_service = ContactLookupServiceFake(
        result(
            LookupOutcome.FAILED,
            error="provider_identity_conflict",
        )
    )
    conflict_client, _repository, _auth, conflict_access = product_client(
        contact_lookup_service=conflict_service
    )
    conflict_response = conflict_client.post(
        "/api/v1/contacts/lookup",
        headers=headers(conflict_access),
        json=REQUEST,
    )

    assert quota_response.status_code == 429
    assert quota_response.json()["contact"]["phone"] == ""
    assert conflict_response.status_code == 409
    assert conflict_response.json()["meta"]["safe_error_code"] == (
        "provider_identity_conflict"
    )


def test_poll_hides_unowned_event_and_contacts_never_accept_internal_api_key() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    assert client.get(
        "/api/v1/contacts/lookups/71",
        headers={"X-API-Key": "a" * 32},
    ).status_code == 401

    service.event_visible = False
    other = client.get(
        "/api/v1/contacts/lookups/71",
        headers=headers(access),
    )

    assert other.status_code == 404


def test_conflicting_profile_alias_is_rejected_before_service_call() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    response = client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json={
            **REQUEST,
            "profile_url": "https://www.facebook.com/another.user",
        },
    )

    assert response.status_code == 400
    assert service.lookup_calls == []


def test_contact_lookup_requires_bearer_even_with_valid_web_cookie_csrf() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )
    client.cookies.set("lead_finder_access", access)
    client.cookies.set("lead_finder_csrf", "csrf-value")

    response = client.post(
        "/api/v1/contacts/lookup",
        headers={
            "Origin": WEB_ORIGIN,
            "X-CSRF-Token": "csrf-value",
            "X-Installation-ID": str(INSTALLATION_ID),
        },
        json=REQUEST,
    )

    assert response.status_code == 401
    assert service.lookup_calls == []


def test_facebook_alias_grammar_rejects_unicode_lookalikes() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    response = client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json={"facebook_uid": "１００１２３"},
    )

    assert response.status_code == 400
    assert service.lookup_calls == []


def test_contact_lookup_is_rate_limited_before_service_call() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.NOT_FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service,
        rate_limit_policies={
            "contact_lookup": RateLimitPolicy(1, 60),
            "contact_poll": RateLimitPolicy(1, 60),
        },
    )

    first = client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json=REQUEST,
    )
    limited = client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json=REQUEST,
    )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert len(service.lookup_calls) == 1


def test_profile_url_only_derives_provider_username() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.NOT_FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    response = client.post(
        "/api/v1/contacts/lookup",
        headers=headers(access),
        json={"profile_url": "https://www.facebook.com/sample.user"},
    )

    assert response.status_code == 200
    request = service.lookup_calls[0][2]
    assert request.username == "sample.user"


def test_batch_lookup_returns_ordered_manual_scan_results() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    response = client.post(
        "/api/v1/contacts/batch-lookup",
        headers=headers(access),
        json={
            "items": [
                {
                    **REQUEST,
                    "source_type": "comment_author",
                    "source_url": "https://www.facebook.com/groups/123/posts/456",
                },
                {
                    **REQUEST,
                    "source_type": "comment_author",
                    "source_url": "https://www.facebook.com/groups/123/posts/456",
                },
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["detected_count"] == 2
    assert body["unique_count"] == body["processed_count"] == 1
    assert body["items"][1]["duplicate_of"] == 0
    assert len(service.lookup_calls) == 1
    assert service.scan_contexts[0].mode == "manual_loaded"
    assert service.scan_contexts[0].source_type == "comment_author"


def test_batch_lookup_rejects_unknown_fields_and_foreign_source_urls() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )

    response = client.post(
        "/api/v1/contacts/batch-lookup",
        headers=headers(access),
        json={
            "items": [
                {
                    **REQUEST,
                    "source_type": "member",
                    "source_url": "https://evil.example/groups/123",
                    "html": "<html>secret</html>",
                }
            ]
        },
    )

    assert response.status_code == 400
    assert service.lookup_calls == []
