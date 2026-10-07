from urllib.parse import parse_qs, urlsplit
from datetime import timedelta

from fastapi.testclient import TestClient

from fb_crawl.api.app import create_app
from fb_crawl.api.config import ApiSettings
from fb_crawl.composition.product import ProductServices
from tests.unit.auth.test_service import EMAIL, NOW, PASSWORD, service_parts


def test_reset_link_has_a_public_private_landing_page():
    app = create_app(
        ApiSettings(api_key="k" * 32),
        job_service=object(), job_repository=object(), user_repository=object(),
        readiness=lambda _migration: True,
    )
    response = TestClient(app).get('/reset-password?token=synthetic-reset-secret')
    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'no-store' in response.headers['cache-control']
    assert 'synthetic-reset-secret' not in response.text
    assert '/api/v1/auth/reset-password' in response.text
    assert '/api/v1/auth/forgot-password' in response.text
    assert '/api/v1/auth/verify-reset-code' in response.text
    assert 'autocomplete="one-time-code"' in response.text
    assert 'autocomplete="new-password"' in response.text


def test_verified_code_grant_changes_password_once_and_revokes_sessions(service_parts):
    service, repository, email, _limiter, tokens = service_parts
    service.register(EMAIL, PASSWORD, NOW, ip_address='127.0.0.1')
    app = create_app(
        ApiSettings(api_key='k' * 32),
        job_service=object(), job_repository=object(), user_repository=object(),
        readiness=lambda _migration: True,
        product_services=ProductServices(service, repository, tokens), clock=lambda: NOW,
    )
    client = TestClient(app)
    assert client.post('/api/v1/auth/forgot-password', json={'email': EMAIL}).status_code == 200
    verified=client.post('/api/v1/auth/verify-reset-code',json={'email':EMAIL,'code':email.resets[-1][1]})
    assert verified.status_code==200
    token=verified.json()['reset_token']
    payload = {'token': token, 'new_password': 'replacement-password-123'}
    assert client.post('/api/v1/auth/reset-password', json=payload).status_code == 200
    assert repository.hasher.verify(repository.account.password_hash, payload['new_password'])
    assert not repository.hasher.verify(repository.account.password_hash, PASSWORD)
    assert repository.revoked_accounts == [repository.account.id]
    reused = client.post('/api/v1/auth/reset-password', json=payload)
    assert reused.status_code == 400
    assert token not in reused.text


def test_expired_reset_link_does_not_change_password(service_parts):
    service, repository, email, _limiter, tokens = service_parts
    repository.add_account(verified=True)
    token='legacy-reset-token'
    repository.create_account_token(repository.account.id,'password_reset',tokens.digest_opaque(token),NOW+timedelta(hours=1),NOW)
    app = create_app(
        ApiSettings(api_key='k' * 32),
        job_service=object(), job_repository=object(), user_repository=object(),
        readiness=lambda _migration: True,
        product_services=ProductServices(service, repository, tokens),
        clock=lambda: NOW + timedelta(hours=2),
    )
    response = TestClient(app).post('/api/v1/auth/reset-password', json={
        'token': token, 'new_password': 'replacement-password-123',
    })
    assert response.status_code == 400
    assert repository.password_updates == []
    assert repository.revoked_accounts == []
    assert token not in response.text
