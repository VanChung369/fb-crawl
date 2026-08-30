from __future__ import annotations

from email.message import EmailMessage

import pytest

from fb_crawl.auth.email import EmailDeliveryFailed, SmtpEmailDelivery


class RecordingSmtp:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.connection = (host, port, timeout)
        self.calls: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def starttls(self) -> None:
        self.calls.append(("starttls",))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append(("send", message))


def test_smtp_delivery_uses_tls_login_and_contains_only_supplied_link() -> None:
    instances: list[RecordingSmtp] = []

    def factory(host: str, port: int, timeout: float) -> RecordingSmtp:
        smtp = RecordingSmtp(host, port, timeout)
        instances.append(smtp)
        return smtp

    delivery = SmtpEmailDelivery(
        host="smtp.example.com",
        port=587,
        username="mailer",
        password="private-password",
        sender="Lead Finder <noreply@example.com>",
        smtp_factory=factory,
    )
    delivery.send_verification(
        "person@example.com",
        "https://leads.example.com/verify-email?token=opaque",
    )

    smtp = instances[0]
    assert smtp.connection == ("smtp.example.com", 587, 10.0)
    assert smtp.calls[0] == ("starttls",)
    assert smtp.calls[1] == ("login", "mailer", "private-password")
    message = smtp.calls[2][1]
    assert isinstance(message, EmailMessage)
    assert message["To"] == "person@example.com"
    assert "token=opaque" in message.get_content()


def test_smtp_failure_is_sanitized() -> None:
    def fail(*_args: object, **_kwargs: object):
        raise OSError("private-password at smtp.internal")

    delivery = SmtpEmailDelivery(
        host="smtp.internal",
        port=587,
        username="mailer",
        password="private-password",
        sender="noreply@example.com",
        smtp_factory=fail,
    )

    with pytest.raises(EmailDeliveryFailed) as captured:
        delivery.send_password_reset(
            "person@example.com",
            "https://leads.example.com/reset-password?token=opaque",
        )
    assert captured.value.code == "email_delivery_failed"
    assert "private-password" not in str(captured.value)
    assert "smtp.internal" not in str(captured.value)
