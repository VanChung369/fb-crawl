from __future__ import annotations

from uuid import UUID
import logging

from fb_crawl.contacts.models import LookupOutcome
from tests.unit.api.product_auth_fakes import INSTALLATION_ID
from tests.unit.api.test_contact_routes import (
    ContactLookupServiceFake,
    REQUEST,
    result,
)
from tests.unit.api.test_product_auth_routes import product_client


def test_valid_request_id_is_preserved_and_invalid_value_is_replaced() -> None:
    service = ContactLookupServiceFake(result(LookupOutcome.NOT_FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )
    auth_headers = {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }
    supplied = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    preserved = client.post(
        "/api/v1/contacts/lookup",
        headers={**auth_headers, "X-Request-ID": supplied},
        json=REQUEST,
    )
    replaced = client.post(
        "/api/v1/contacts/lookup",
        headers={**auth_headers, "X-Request-ID": "private-not-a-uuid"},
        json=REQUEST,
    )

    assert preserved.headers["X-Request-ID"] == supplied
    assert UUID(replaced.headers["X-Request-ID"])
    assert replaced.headers["X-Request-ID"] != "private-not-a-uuid"
    assert service.request_ids == [
        supplied,
        replaced.headers["X-Request-ID"],
    ]


def test_unexpected_api_log_contains_sanitized_request_id(caplog) -> None:
    class RaisingService(ContactLookupServiceFake):
        def lookup(self, *_args, **_kwargs):
            raise RuntimeError("private provider trace")

    service = RaisingService(result(LookupOutcome.NOT_FOUND))
    client, _repository, _auth, access = product_client(
        contact_lookup_service=service
    )
    request_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    with caplog.at_level(logging.ERROR, logger="fb_crawl.api.app"):
        response = client.post(
            "/api/v1/contacts/lookup",
            headers={
                "Authorization": f"Bearer {access}",
                "X-Installation-ID": str(INSTALLATION_ID),
                "X-Request-ID": request_id,
            },
            json=REQUEST,
        )

    assert response.status_code == 500
    record = next(
        item for item in caplog.records if item.getMessage() == "Unhandled API error."
    )
    assert record.request_id == request_id
    assert "private provider trace" not in record.getMessage()
