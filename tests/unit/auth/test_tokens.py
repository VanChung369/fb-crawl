from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from fb_crawl.auth.tokens import (
    AccessTokenExpired,
    InvalidAccessToken,
    TokenService,
)


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
SESSION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
JWT_SECRET = "j" * 32
HMAC_SECRET = "h" * 32


def _service(**overrides: object) -> TokenService:
    values = {
        "jwt_secret": JWT_SECRET,
        "token_hmac_secret": HMAC_SECRET,
        **overrides,
    }
    return TokenService(**values)


def test_access_claims_contain_identity_not_authorization_state() -> None:
    service = _service()

    token = service.issue_access(7, SESSION_ID, 9, NOW)
    claims = service.decode_access(token, NOW)

    assert (claims.account_id, claims.session_id, claims.device_id) == (
        7,
        SESSION_ID,
        9,
    )
    assert claims.issued_at == NOW
    assert claims.expires_at == NOW + timedelta(minutes=15)
    assert not hasattr(claims, "role")
    assert not hasattr(claims, "plan")
    assert not hasattr(claims, "quota")


def test_access_token_expiry_is_checked_against_injected_time() -> None:
    service = _service()
    token = service.issue_access(7, SESSION_ID, 9, NOW)

    with pytest.raises(AccessTokenExpired):
        service.decode_access(token, NOW + timedelta(minutes=15))


def test_wrong_audience_signature_and_malformed_claims_are_rejected() -> None:
    token = _service(audience="another-product").issue_access(
        7, SESSION_ID, 9, NOW
    )
    with pytest.raises(InvalidAccessToken):
        _service().decode_access(token, NOW)

    token = TokenService(
        jwt_secret="x" * 32,
        token_hmac_secret=HMAC_SECRET,
    ).issue_access(7, SESSION_ID, 9, NOW)
    with pytest.raises(InvalidAccessToken):
        _service().decode_access(token, NOW)

    with pytest.raises(InvalidAccessToken):
        _service().decode_access("not-a-jwt", NOW)


def test_opaque_tokens_are_random_and_compared_by_hmac_digest() -> None:
    service = _service()
    first = service.new_opaque_token()
    second = service.new_opaque_token()

    assert first != second
    assert len(first) >= 32
    digest = service.digest_opaque(first)
    assert first not in digest
    assert service.matches_opaque(first, digest) is True
    assert service.matches_opaque(second, digest) is False
    assert TokenService(
        jwt_secret=JWT_SECRET,
        token_hmac_secret="z" * 32,
    ).digest_opaque(first) != digest


def test_token_service_rejects_naive_time_and_invalid_identity() -> None:
    service = _service()

    with pytest.raises(ValueError, match="timezone-aware"):
        service.issue_access(7, SESSION_ID, 9, NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="positive"):
        service.issue_access(0, SESSION_ID, 9, NOW)
