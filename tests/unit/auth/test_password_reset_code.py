from datetime import timedelta
import pytest
from fb_crawl.accounts.repository import InvalidAccountToken
from tests.unit.auth.test_service import EMAIL, NOW, PASSWORD, service_parts


def test_recovery_emails_only_a_code_and_exchanges_it_for_a_single_use_token(service_parts):
    service, repository, email, limiter, tokens = service_parts
    repository.add_account(verified=True)
    assert service.forgot_password(EMAIL, NOW, ip_address='203.0.113.1').accepted
    code = email.resets[-1][1]
    assert len(code) == 6 and code.isascii() and code.isdigit()
    assert not repository.tokens  # No password-reset capability before verification.
    assert repository.reset_codes[7][0] != code
    with pytest.raises(InvalidAccountToken):
        service.reset_password(code, PASSWORD, NOW, ip_address='203.0.113.1')
    verified = service.verify_password_reset_code(EMAIL, code, NOW, ip_address='203.0.113.1')
    assert verified.reset_token != code and verified.expires_in == 600
    assert tokens.digest_opaque(verified.reset_token) in repository.tokens
    with pytest.raises(InvalidAccountToken):
        service.verify_password_reset_code(EMAIL, code, NOW, ip_address='203.0.113.1')
    service.reset_password(verified.reset_token, 'a new secure password', NOW, ip_address='203.0.113.1')
    with pytest.raises(InvalidAccountToken):
        service.reset_password(verified.reset_token, PASSWORD, NOW, ip_address='203.0.113.1')
    assert any(call[0]=='verify_password_reset_code' and call[1]==EMAIL for call in limiter.calls)


def test_wrong_or_expired_codes_never_issue_a_reset_token(service_parts):
    service, repository, email, _, _ = service_parts
    repository.add_account(verified=True)
    service.forgot_password(EMAIL, NOW, ip_address='203.0.113.1')
    code = email.resets[-1][1]
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(5):
        with pytest.raises(InvalidAccountToken):
            service.verify_password_reset_code(EMAIL, wrong, NOW, ip_address='203.0.113.1')
    with pytest.raises(InvalidAccountToken):
        service.verify_password_reset_code(EMAIL, code, NOW, ip_address='203.0.113.1')
    assert not repository.tokens
    service.forgot_password(EMAIL, NOW+timedelta(minutes=1), ip_address='203.0.113.1')
    with pytest.raises(InvalidAccountToken):
        service.verify_password_reset_code(EMAIL, email.resets[-1][1], NOW+timedelta(minutes=11), ip_address='203.0.113.1')


def test_resending_invalidates_the_previous_verified_reset_grant(service_parts):
    service, repository, email, _, _ = service_parts
    repository.add_account(verified=True)
    service.forgot_password(EMAIL, NOW, ip_address='203.0.113.1')
    verified=service.verify_password_reset_code(EMAIL,email.resets[-1][1],NOW,ip_address='203.0.113.1')
    service.forgot_password(EMAIL,NOW+timedelta(minutes=1),ip_address='203.0.113.1')
    with pytest.raises(InvalidAccountToken):
        service.reset_password(verified.reset_token,PASSWORD,NOW+timedelta(minutes=1),ip_address='203.0.113.1')


def test_unknown_accounts_return_generic_send_response_and_invalid_code(service_parts):
    service, repository, email, _, _ = service_parts
    assert service.forgot_password(EMAIL,NOW,ip_address='203.0.113.1').accepted
    assert not email.resets
    with pytest.raises(InvalidAccountToken):
        service.verify_password_reset_code(EMAIL,'123456',NOW,ip_address='203.0.113.1')


def test_replaced_code_and_expired_verified_grant_are_rejected(service_parts,monkeypatch):
    service, repository, email, _, _ = service_parts
    repository.add_account(verified=True)
    codes=iter([111111,222222])
    monkeypatch.setattr('fb_crawl.auth.service.secrets.randbelow',lambda _n:next(codes))
    service.forgot_password(EMAIL,NOW,ip_address='203.0.113.1')
    service.forgot_password(EMAIL,NOW+timedelta(minutes=1),ip_address='203.0.113.1')
    with pytest.raises(InvalidAccountToken):
        service.verify_password_reset_code(EMAIL,'111111',NOW+timedelta(minutes=1),ip_address='203.0.113.1')
    grant=service.verify_password_reset_code(EMAIL,'222222',NOW+timedelta(minutes=1),ip_address='203.0.113.1')
    with pytest.raises(InvalidAccountToken):
        service.reset_password(grant.reset_token,PASSWORD,NOW+timedelta(minutes=11),ip_address='203.0.113.1')
    assert not repository.password_updates
