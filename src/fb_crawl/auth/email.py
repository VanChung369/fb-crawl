from __future__ import annotations

from email.message import EmailMessage
import smtplib
from typing import Protocol

from fb_crawl.core.exceptions import FbCrawlError


class EmailDeliveryFailed(FbCrawlError):
    code = "email_delivery_failed"


class EmailDeliveryPort(Protocol):
    def send_verification(self, email: str, url: str, code: str = "") -> None: ...

    def send_password_reset(self, email: str, url: str) -> None: ...

    def send_password_reset_code(self, email: str, code: str) -> None: ...


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

    def send_verification(self, email: str, url: str, code: str = "") -> None:
        if code:
            subject = f"[Lead Finder] Mã xác nhận của bạn là: {code}"
            body = (
                f"Xin chào,\n\n"
                f"Mã xác nhận tài khoản Lead Finder của bạn là:\n\n"
                f"    {code}\n\n"
                f"Vui lòng nhập mã này vào ứng dụng để hoàn tất kích hoạt tài khoản.\n"
                f"Mã có hiệu lực trong 24 giờ.\n\n"
                f"(Nếu bạn không yêu cầu mã này, vui lòng bỏ qua email)."
            )
        else:
            subject = "Verify your Lead Finder email"
            body = (
                "Verify your Lead Finder account using this link:\n\n"
                f"{url}\n\nThis link expires in 24 hours."
            )
        self._send(
            recipient=email,
            subject=subject,
            body=body,
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

    def send_password_reset_code(self, email: str, code: str) -> None:
        self._send(recipient=email, subject='Mã đặt lại mật khẩu Lead Finder', body=(
            f'Mã đặt lại mật khẩu của bạn là: {code}\n\n'
            'Nhập mã này trong Lead Finder để xác nhận và chọn mật khẩu mới. '
            'Mã có hiệu lực trong 10 phút và chỉ dùng một lần.\n\n'
            'Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này. '
            'Không chia sẻ mã với bất kỳ ai.'
        ))

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
