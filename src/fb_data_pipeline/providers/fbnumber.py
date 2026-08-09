from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from fb_data_pipeline.config import PipelineSettings
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProviderResult,
    ProviderStatus,
)
from fb_data_pipeline.core.phone import InvalidPhoneNumber, normalize_phone


PHONE_KEYS = frozenset({"phone", "phone_number", "phones", "phone_numbers"})


def _correlation_id(payload: Any, response: httpx.Response) -> str:
    for header in ("x-request-id", "x-correlation-id", "request-id"):
        if response.headers.get(header):
            return response.headers[header].strip()
    if isinstance(payload, Mapping):
        for key in ("correlation_id", "request_id", "requestId"):
            value = payload.get(key)
            if value:
                return str(value).strip()
    return ""


def _phone_candidates(payload: Any) -> tuple[str, ...]:
    found: list[str] = []

    def collect(value: Any, *, phone_context: bool = False) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                collect(child, phone_context=str(key).casefold() in PHONE_KEYS)
            return
        if isinstance(value, list | tuple):
            for child in value:
                collect(child, phone_context=phone_context)
            return
        if phone_context and value is not None:
            candidate = str(value).strip()
            if candidate:
                found.append(candidate)

    collect(payload)
    return tuple(dict.fromkeys(found))


class FBNumberProvider:
    """Adapter for the configurable FBNumber phone-search endpoint."""

    name = "fbnumber"

    def __init__(
        self,
        *,
        api_url: str,
        api_token: str,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        default_country_code: str = "84",
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_url = api_url
        self.default_country_code = default_country_code
        self.max_retries = max_retries
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        token_value = (
            f"{auth_scheme} {api_token}".strip() if auth_scheme else api_token
        )
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None
        self._headers = {auth_header: token_value, "Accept": "application/json"}

    @classmethod
    def from_settings(
        cls,
        settings: PipelineSettings,
        *,
        client: httpx.Client | None = None,
    ) -> FBNumberProvider:
        settings.require_fb_number()
        return cls(
            api_url=settings.fb_number_api_url,
            api_token=settings.fb_number_api_token,
            auth_header=settings.fb_number_auth_header,
            auth_scheme=settings.fb_number_auth_scheme,
            timeout_seconds=settings.fb_number_timeout_seconds,
            max_retries=settings.fb_number_max_retries,
            default_country_code=settings.default_country_code,
            client=client,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, identity: FacebookIdentity) -> ProviderResult:
        checked_at = self._clock()
        if not identity.uid and not identity.username:
            return ProviderResult(
                provider=self.name,
                status=ProviderStatus.FAILED,
                checked_at=checked_at,
                error_code="provider_identity_insufficient",
            )
        request_body = {
            "username": identity.username,
            "name": identity.name,
            "uid": identity.uid,
        }

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(
                    self.api_url,
                    headers=self._headers,
                    json=request_body,
                )
            except httpx.TransportError:
                if attempt < self.max_retries:
                    self._sleeper(0.25 * (2**attempt))
                    continue
                return ProviderResult(
                    provider=self.name,
                    status=ProviderStatus.FAILED,
                    checked_at=checked_at,
                    error_code="provider_transport_error",
                )

            if response.status_code == 429:
                if attempt < self.max_retries:
                    self._sleeper(0.25 * (2**attempt))
                    continue
                return ProviderResult(
                    provider=self.name,
                    status=ProviderStatus.RATE_LIMITED,
                    checked_at=checked_at,
                    correlation_id=_correlation_id({}, response),
                    error_code="provider_rate_limited",
                )

            if response.status_code >= 500 and attempt < self.max_retries:
                self._sleeper(0.25 * (2**attempt))
                continue

            if response.status_code == 404:
                return ProviderResult(
                    provider=self.name,
                    status=ProviderStatus.NOT_FOUND,
                    checked_at=checked_at,
                    correlation_id=_correlation_id({}, response),
                )

            if not 200 <= response.status_code < 300:
                return ProviderResult(
                    provider=self.name,
                    status=ProviderStatus.FAILED,
                    checked_at=checked_at,
                    correlation_id=_correlation_id({}, response),
                    error_code=f"provider_http_{response.status_code}",
                )

            try:
                response_body = response.json()
            except ValueError:
                return ProviderResult(
                    provider=self.name,
                    status=ProviderStatus.FAILED,
                    checked_at=checked_at,
                    correlation_id=_correlation_id({}, response),
                    error_code="provider_invalid_json",
                )

            correlation_id = _correlation_id(response_body, response)
            evidence: list[PhoneEvidence] = []
            seen: set[str] = set()
            for phone in _phone_candidates(response_body):
                try:
                    normalized = normalize_phone(
                        phone,
                        default_country_code=self.default_country_code,
                    )
                except InvalidPhoneNumber:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                evidence.append(
                    PhoneEvidence(
                        phone_number=phone,
                        normalized_phone=normalized,
                        source="external:fbnumber",
                        captured_at=checked_at,
                        confidence="provider",
                        provider=self.name,
                        correlation_id=correlation_id,
                    )
                )

            return ProviderResult(
                provider=self.name,
                status=(
                    ProviderStatus.FOUND
                    if evidence
                    else ProviderStatus.NOT_FOUND
                ),
                evidence=tuple(evidence),
                checked_at=checked_at,
                correlation_id=correlation_id,
            )

        raise AssertionError("Provider retry loop exhausted unexpectedly.")

