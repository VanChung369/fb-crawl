from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from fb_data_pipeline.config import PipelineSettings
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProfileData,
    ProviderResult,
    ProviderStatus,
)
from fb_data_pipeline.core.phone import InvalidPhoneNumber, normalize_phone
from fb_data_pipeline.detectors.vietnamese import infer_profile_attributes


def _selected_data(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    nested = payload.get("data")
    return nested if isinstance(nested, Mapping) else payload


def _extract_profile_data(payload: Any, checked_at: datetime) -> tuple[ProfileData, str]:
    data = _selected_data(payload)
    if data is None:
        return ProfileData(), ""

    gender = str(data.get("gender") or data.get("sex") or "").strip()
    birthday = str(data.get("birthday") or data.get("birth_date") or data.get("birthDate") or data.get("dob") or "").strip()
    location = str(data.get("location") or data.get("address") or data.get("city") or data.get("current_city") or "").strip()
    name = str(data.get("name") or data.get("display_name") or data.get("fullname") or "").strip()

    address, gender = infer_profile_attributes(name, location, gender)

    profile = ProfileData(
        address=address,
        birth_date=birthday,
        gender=gender,
        source_url="external:fbnumber",
        observed_at=checked_at,
    )
    return profile, name


UID_KEYS = ("uid", "user_id", "facebook_uid")
USERNAME_KEYS = ("username", "facebook_username")
FACEBOOK_UID = re.compile(r"[0-9]+")
FACEBOOK_USERNAME = re.compile(r"[A-Za-z0-9.]+")


def _provider_uid(data: Mapping[str, Any]) -> str:
    for key in UID_KEYS:
        raw = data.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        value = str(raw).strip()
        if FACEBOOK_UID.fullmatch(value):
            return value
    return ""


def _provider_username(data: Mapping[str, Any]) -> str:
    for key in USERNAME_KEYS:
        raw = data.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        value = str(raw).strip()
        if FACEBOOK_USERNAME.fullmatch(value):
            return value
    return ""


def _resolved_identity(payload: Any) -> FacebookIdentity | None:
    data = _selected_data(payload)
    if data is None:
        return None
    resolved = FacebookIdentity(
        uid=_provider_uid(data),
        username=_provider_username(data),
    )
    return resolved if resolved.uid or resolved.username else None


def _identity_conflicts(
    requested: FacebookIdentity,
    resolved: FacebookIdentity | None,
) -> bool:
    if resolved is None:
        return False
    return bool(
        (requested.uid and resolved.uid and requested.uid != resolved.uid)
        or (
            requested.username
            and resolved.username
            and requested.username.casefold() != resolved.username.casefold()
        )
    )



PHONE_KEYS = frozenset(
    {
        "phone",
        "phone_number",
        "phones",
        "phone_numbers",
        "number",
        "number1",
        "number2",
        "number3",
        "mobile",
        "tel",
        "telephone",
        "sdt",
        "phonenumber",
    }
)


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


def _retry_after(
    response: httpx.Response,
    received_at: datetime,
) -> datetime | None:
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    if seconds < 0:
        return None
    return received_at + timedelta(seconds=seconds)


def _phone_candidates(payload: Any) -> tuple[str, ...]:
    found: list[str] = []

    def collect(value: Any, *, phone_context: bool = False) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                k = str(key).casefold()
                is_phone_key = (
                    k in PHONE_KEYS
                    or k.startswith("number")
                    or k.startswith("phone")
                    or k.startswith("mobile")
                ) and not k.endswith("provider")
                collect(child, phone_context=is_phone_key)
            return
        if isinstance(value, list | tuple):
            for child in value:
                collect(child, phone_context=phone_context)
            return
        if phone_context and value is not None and not isinstance(value, bool):
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
        return self._search(identity, allow_resolved_uid_retry=True)

    def _search(
        self,
        identity: FacebookIdentity,
        *,
        allow_resolved_uid_retry: bool,
    ) -> ProviderResult:
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
                retry_after = _retry_after(response, self._clock())
                if retry_after is not None:
                    return ProviderResult(
                        provider=self.name,
                        status=ProviderStatus.RATE_LIMITED,
                        checked_at=checked_at,
                        correlation_id=_correlation_id({}, response),
                        error_code="provider_rate_limited",
                        retry_after=retry_after,
                    )
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
            status = response_body.get("status") if isinstance(response_body, Mapping) else None
            if status is False or (
                isinstance(status, str)
                and status.strip().casefold() in {"error"}
            ):
                return ProviderResult(
                    provider=self.name,
                    status=ProviderStatus.FAILED,
                    checked_at=checked_at,
                    correlation_id=correlation_id,
                    error_code="provider_lookup_failed",
                )
            resolved_identity = _resolved_identity(response_body)
            if _identity_conflicts(identity, resolved_identity):
                return ProviderResult(
                    provider=self.name,
                    status=ProviderStatus.FAILED,
                    checked_at=checked_at,
                    correlation_id=correlation_id,
                    error_code="provider_identity_conflict",
                )

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

            if (
                allow_resolved_uid_retry
                and not evidence
                and not identity.uid
                and resolved_identity is not None
                and resolved_identity.uid
            ):
                return self._search(
                    FacebookIdentity(
                        uid=resolved_identity.uid,
                        username=identity.username or resolved_identity.username,
                        name=identity.name,
                        profile_url=identity.profile_url,
                    ),
                    allow_resolved_uid_retry=False,
                )

            profile, extracted_name = _extract_profile_data(response_body, checked_at)
            has_data = bool(
                evidence
                or not profile.is_empty
                or extracted_name
                or resolved_identity
            )

            return ProviderResult(
                provider=self.name,
                status=(
                    ProviderStatus.FOUND
                    if has_data
                    else ProviderStatus.NOT_FOUND
                ),
                resolved_identity=resolved_identity,
                evidence=tuple(evidence),
                checked_at=checked_at,
                correlation_id=correlation_id,
                profile=profile,
                name=extracted_name,
            )

        raise AssertionError("Provider retry loop exhausted unexpectedly.")
