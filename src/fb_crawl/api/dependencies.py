from __future__ import annotations

import hmac

from fastapi import Header

from fb_crawl.core.exceptions import FbCrawlError


AUTH_ERROR_BODY = {
    "code": "api_unauthorized",
    "message": "API authentication failed.",
}


class ApiAuthenticationError(FbCrawlError):
    code = "api_unauthorized"

    def __init__(self) -> None:
        super().__init__("API authentication failed.")


class ApiKeyAuth:
    """FastAPI dependency backed by a constant-time shared-key comparison."""

    __slots__ = ("_expected_key_bytes",)

    def __init__(self, expected_key: str) -> None:
        self._expected_key_bytes = expected_key.encode("utf-8")

    def verify(self, provided_key: str | None) -> None:
        candidate = provided_key if isinstance(provided_key, str) else ""
        candidate_bytes = candidate.encode("utf-8")
        if not hmac.compare_digest(candidate_bytes, self._expected_key_bytes):
            raise ApiAuthenticationError

    async def __call__(
        self,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> None:
        self.verify(x_api_key)
