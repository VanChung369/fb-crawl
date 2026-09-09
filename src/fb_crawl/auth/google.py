from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import httpx

from fb_crawl.core.exceptions import ValidationError


class GoogleAuthError(ValidationError):
    code = "google_auth_failed"


@dataclass(frozen=True, slots=True)
class GoogleUserInfo:
    email: str
    email_verified: bool
    google_user_id: str
    display_name: str | None = None


class GoogleTokenVerifierPort(Protocol):
    def verify_id_token(self, id_token: str) -> GoogleUserInfo: ...


class GoogleTokenVerifier:
    def __init__(
        self,
        *,
        allowed_client_ids: tuple[str, ...] | None = None,
        tokeninfo_url: str = "https://oauth2.googleapis.com/tokeninfo",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._allowed_client_ids = (
            set(allowed_client_ids) if allowed_client_ids else None
        )
        self._tokeninfo_url = tokeninfo_url
        self._timeout_seconds = timeout_seconds

    def verify_id_token(self, id_token: str) -> GoogleUserInfo:
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.get(
                    self._tokeninfo_url,
                    params={"id_token": id_token},
                )
        except Exception as exc:
            raise GoogleAuthError(
                "Failed to reach Google token verification service."
            ) from exc

        if response.status_code != 200:
            raise GoogleAuthError("Invalid Google ID token.")

        data = response.json()
        email = data.get("email")
        if not email:
            raise GoogleAuthError("Google token does not contain an email.")

        email_verified_raw = data.get("email_verified")
        email_verified = email_verified_raw is True or email_verified_raw == "true"
        if not email_verified:
            raise GoogleAuthError("Google email is not verified.")

        aud = data.get("aud")
        if self._allowed_client_ids and aud not in self._allowed_client_ids:
            raise GoogleAuthError("Google token audience mismatch.")

        return GoogleUserInfo(
            email=email,
            email_verified=True,
            google_user_id=data.get("sub", ""),
            display_name=data.get("name"),
        )
