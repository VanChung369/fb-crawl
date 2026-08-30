from __future__ import annotations

from email.message import EmailMessage
import smtplib
from typing import Protocol

from fb_crawl.core.exceptions import FbCrawlError


class EmailDeliveryFailed(FbCrawlError):
    code = "email_delivery_failed"


class EmailDeliveryPort(Protocol):
    def send_verification(self, email: str, url: str) -> None: ...

    def send_password_reset(self, email: str, url: str) -> None: ...


class SmtpEmailDelivery:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        timeout_seconds: float = 10.0,
        smtp_factory=smtplib.SMTP,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._timeout_seconds = timeout_seconds
        self._smtp_factory = smtp_factory

    def send_verification(self, email: str, url: str) -> None:
        self._send(
            recipient=email,
            subject="Verify your Lead Finder email",
            body=(
                "Verify your Lead Finder account using this link:\n\n"
                f"{url}\n\nThis link expires in 24 hours."
            ),
        )

    def send_password_reset(self, email: str, url: str) -> None:
        self._send(
            recipient=email,
            subject="Reset your Lead Finder password",
            body=(
                "Reset your Lead Finder password using this link:\n\n"
                f"{url}\n\nThis link expires in one hour."
            ),
        )

    def _send(self, *, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        try:
            with self._smtp_factory(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
            ) as smtp:
                smtp.starttls()
                if self._username:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailDeliveryFailed(
                "The account email could not be delivered."
            ) from error
