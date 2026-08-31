from datetime import UTC, datetime, timedelta

import httpx

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneSlot,
    ProviderStatus,
)
from fb_data_pipeline.providers.fbnumber import FBNumberProvider


def test_fbnumber_maps_identity_and_returns_phone_1() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["Authorization"]
        observed["body"] = request.content.decode()
        return httpx.Response(
            200,
            headers={"x-request-id": "req-123"},
            json={"data": {"phones": ["0912 345 678", "+84 912 345 678"]}},
        )

    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )

    result = provider.search(
        FacebookIdentity(uid="10001", username="a.user", name="A")
    )

    assert result.status is ProviderStatus.FOUND
    assert len(result.evidence) == 1
    assert result.evidence[0].normalized_phone == "+84912345678"
    assert result.evidence[0].slot is PhoneSlot.PHONE_1
    assert result.correlation_id == "req-123"
    assert observed["authorization"] == "Bearer secret"
    assert '"uid":"10001"' in str(observed["body"]).replace(" ", "")


def test_fbnumber_returns_consistent_resolved_identity_from_selected_data() -> None:
    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "data": {
                            "facebook_uid": 100123,
                            "facebook_username": "sample.user",
                            "phone": "0981 234 567",
                        }
                    },
                )
            )
        ),
    )

    result = provider.search(FacebookIdentity(username="sample.user"))

    assert result.status is ProviderStatus.FOUND
    assert result.resolved_identity == FacebookIdentity(
        uid="100123",
        username="sample.user",
    )


def test_fbnumber_rejects_conflicting_returned_username() -> None:
    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "uid": "100123",
                        "username": "other.user",
                        "phone": "0981 234 567",
                    },
                )
            )
        ),
    )

    result = provider.search(FacebookIdentity(username="sample.user"))

    assert result.status is ProviderStatus.FAILED
    assert result.error_code == "provider_identity_conflict"
    assert result.evidence == ()


def test_fbnumber_rejects_conflicting_returned_uid() -> None:
    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "uid": "100999",
                        "phone": "0981 234 567",
                    },
                )
            )
        ),
    )

    result = provider.search(FacebookIdentity(uid="100123"))

    assert result.status is ProviderStatus.FAILED
    assert result.error_code == "provider_identity_conflict"
    assert result.evidence == ()


def test_fbnumber_does_not_treat_nested_metadata_as_resolved_identity() -> None:
    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "data": {
                            "phone": "0981 234 567",
                            "metadata": {
                                "uid": "999999",
                                "username": "unrelated.user",
                            },
                        }
                    },
                )
            )
        ),
    )

    result = provider.search(FacebookIdentity(username="sample.user"))

    assert result.status is ProviderStatus.FOUND
    assert result.resolved_identity is None


def test_fbnumber_returns_safe_error_without_response_body() -> None:
    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        max_retries=0,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    500,
                    text="secret provider trace",
                )
            )
        ),
    )

    result = provider.search(FacebookIdentity(uid="10001"))

    assert result.status is ProviderStatus.FAILED
    assert result.error_code == "provider_http_500"
    assert "secret" not in result.error_code


def test_fbnumber_does_not_call_api_without_uid_or_username() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"phone": "0912 345 678"})

    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.search(
        FacebookIdentity(profile_url="https://www.facebook.com/profile-only")
    )

    assert result.status is ProviderStatus.FAILED
    assert result.error_code == "provider_identity_insufficient"
    assert calls == 0


def test_fbnumber_retries_rate_limit_with_bound() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"x-request-id": "rate-1"})

    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        max_retries=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=sleeps.append,
    )

    result = provider.search(FacebookIdentity(uid="10001"))

    assert result.status is ProviderStatus.RATE_LIMITED
    assert result.error_code == "provider_rate_limited"
    assert calls == 2
    assert sleeps == [0.25]


def test_fbnumber_preserves_retry_after_from_rate_limit_response() -> None:
    checked_at = datetime(2026, 8, 31, 3, tzinfo=UTC)
    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        max_retries=0,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    429,
                    headers={"Retry-After": "120"},
                )
            )
        ),
        clock=lambda: checked_at,
    )

    result = provider.search(FacebookIdentity(uid="10001"))

    assert result.status is ProviderStatus.RATE_LIMITED
    assert result.retry_after == checked_at + timedelta(minutes=2)


def test_fbnumber_does_not_retry_rate_limit_with_retry_after() -> None:
    checked_at = datetime(2026, 8, 31, 3, tzinfo=UTC)
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "120"})

    provider = FBNumberProvider(
        api_url="https://api.example.test/v1/phone/search",
        api_token="secret",
        max_retries=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: checked_at,
        sleeper=sleeps.append,
    )

    result = provider.search(FacebookIdentity(uid="10001"))

    assert result.status is ProviderStatus.RATE_LIMITED
    assert result.retry_after == checked_at + timedelta(minutes=2)
    assert calls == 1
    assert sleeps == []
