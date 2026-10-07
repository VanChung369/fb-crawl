from dataclasses import replace

import pytest

from fb_crawl.accounts.models import AccountStatus
from fb_crawl.auth.rate_limit import AuthRateLimited
from fb_crawl.auth.service import InvalidCredentials
from fb_crawl.core.exceptions import ValidationError
from tests.unit.auth.test_service import service_parts, NOW, PASSWORD, EMAIL, INSTALLATION_ID


def test_change_password_requires_old_password_and_revokes_sessions(service_parts):
    service, repository, _email, _limiter, _tokens = service_parts
    repository.add_account(verified=True)
    service.forgot_password(EMAIL, NOW, ip_address='203.0.113.4')
    service.login(EMAIL, PASSWORD, INSTALLATION_ID, 'Chrome', NOW, ip_address='203.0.113.4')
    with pytest.raises(ValidationError):
        service.change_password(7, 'wrong-password', 'new secure password', NOW, ip_address='203.0.113.4')
    assert repository.password_updates == []
    assert all(s.revoked_at is None for s, _ in repository.sessions.values())
    service.change_password(7, PASSWORD, 'new secure password', NOW, ip_address='203.0.113.4')
    assert repository.hasher.verify(repository.account.password_hash, 'new secure password')
    assert all(s.revoked_at == NOW for s, _ in repository.sessions.values())
    assert all(consumed for purpose, _owner, _expiry, consumed in repository.tokens.values() if purpose == 'password_reset')
    with pytest.raises(InvalidCredentials):
        service.login(EMAIL, PASSWORD, INSTALLATION_ID, 'Chrome', NOW, ip_address='203.0.113.4')


@pytest.mark.parametrize('scenario', ['short', 'missing', 'suspended', 'rate_limited', 'stale_hash'])
def test_change_password_rejects_invalid_or_stale_requests_without_writes(service_parts, scenario):
    service, repository, _email, limiter, _tokens = service_parts
    repository.add_account(verified=True)
    if scenario == 'missing':
        repository.account = None
    elif scenario == 'suspended':
        repository.account = replace(repository.account, status=AccountStatus.SUSPENDED)
    elif scenario == 'rate_limited':
        limiter.blocked.add('change_password')
    elif scenario == 'stale_hash':
        repository.change_password = lambda *_args: False
    with pytest.raises(AuthRateLimited if scenario == 'rate_limited' else ValidationError):
        service.change_password(7, PASSWORD, 'short' if scenario == 'short' else 'new secure password', NOW, ip_address='203.0.113.4')
    assert repository.password_updates == []
