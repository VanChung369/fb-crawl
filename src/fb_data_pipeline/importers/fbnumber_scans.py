from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProfileData,
    ProviderResult,
    ProviderStatus,
    UserBundle,
    canonical_profile_url,
)
from fb_data_pipeline.core.phone import InvalidPhoneNumber, normalize_phone
from fb_data_pipeline.detectors.vietnamese import infer_profile_attributes
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict
from fb_data_pipeline.services.pipeline import EnrichedUser

logger = logging.getLogger(__name__)

DEFAULT_FBNUMBER_SCANS_URL = "https://api.fbnumber.com/v1/scans"


def parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_facebook_link(link_fb: str | None) -> tuple[str, str, str]:
    """Parse Facebook link into (uid, username, canonical_profile_url)."""
    raw = str(link_fb or "").strip()
    if not raw:
        return "", "", ""

    canonical = canonical_profile_url(raw)
    parsed = urlparse(raw)

    query_id = parse_qs(parsed.query).get("id", [""])[0].strip()
    if query_id and query_id.isdigit():
        return query_id, "", canonical or f"https://www.facebook.com/profile.php?id={query_id}"

    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    if not parts:
        return "", "", canonical

    # Case: /profile.php without query param or other special routes
    if parts[0].casefold() == "profile.php":
        return query_id if query_id.isdigit() else "", "", canonical

    # Case: /people/username/uid or similar
    if len(parts) >= 3 and parts[0].casefold() == "people" and parts[-1].isdigit():
        return parts[-1], parts[1], canonical

    # Single segment: /100003795620679 or /roaphan
    candidate = parts[0]
    if candidate.isdigit():
        return candidate, "", canonical or f"https://www.facebook.com/{candidate}"

    if candidate.casefold() not in {"groups", "pages", "events", "watch", "story", "stories", "photo", "photos"}:
        return "", candidate, canonical or f"https://www.facebook.com/{candidate}"

    return "", "", canonical


def import_scan_item(
    item: Mapping[str, Any],
    *,
    default_country_code: str = "84",
    fallback_time: datetime | None = None,
) -> EnrichedUser | None:
    """Parse a single FBNumber scan item into an EnrichedUser."""
    link_fb = str(item.get("linkFb") or item.get("link") or "").strip()
    name = str(item.get("name") or "").strip()
    location = str(item.get("location") or item.get("address") or "").strip()
    gender = str(item.get("gender") or "").strip()
    birthday = str(item.get("birthday") or "").strip()
    scan_at_str = item.get("scanAt")
    observed_at = parse_iso_datetime(scan_at_str) or fallback_time or datetime.now(UTC)

    uid, username, canonical_url = parse_facebook_link(link_fb)

    # Check if item provides explicit uid/username
    if not uid and str(item.get("uid") or "").strip().isdigit():
        uid = str(item.get("uid")).strip()
    if not username and str(item.get("username") or "").strip():
        username = str(item.get("username")).strip()

    identity = FacebookIdentity(
        uid=uid,
        username=username,
        name=name,
        profile_url=canonical_url,
    )

    if not identity.is_usable and not name:
        return None

    # Collect phone evidences
    evidence_list: list[PhoneEvidence] = []
    seen_phones: set[str] = set()

    raw_phone_1 = str(item.get("number") or item.get("number1") or item.get("phone") or "").strip()
    provider_1 = str(item.get("numberProvider") or "fbnumber").strip() or "fbnumber"

    if raw_phone_1:
        try:
            norm_1 = normalize_phone(raw_phone_1, default_country_code=default_country_code)
            seen_phones.add(norm_1)
            evidence_list.append(
                PhoneEvidence(
                    phone_number=raw_phone_1,
                    normalized_phone=norm_1,
                    source="external:fbnumber",
                    source_url=canonical_url or link_fb,
                    captured_at=observed_at,
                    confidence="provider",
                    provider=provider_1,
                    correlation_id=str(item.get("_id") or ""),
                )
            )
        except InvalidPhoneNumber:
            pass

    raw_phone_2 = str(item.get("number2") or item.get("phone2") or "").strip()
    provider_2 = str(item.get("number2Provider") or "fbnumber").strip() or "fbnumber"

    if raw_phone_2:
        try:
            norm_2 = normalize_phone(raw_phone_2, default_country_code=default_country_code)
            if norm_2 not in seen_phones:
                seen_phones.add(norm_2)
                evidence_list.append(
                    PhoneEvidence(
                        phone_number=raw_phone_2,
                        normalized_phone=norm_2,
                        source="external:fbnumber",
                        source_url=canonical_url or link_fb,
                        captured_at=observed_at,
                        confidence="provider",
                        provider=provider_2,
                        correlation_id=str(item.get("_id") or ""),
                    )
                )
        except InvalidPhoneNumber:
            pass

    address, gender = infer_profile_attributes(name, location, gender)

    profile = ProfileData(
        address=address,
        birth_date=birthday,
        gender=gender,
        source_url="external:fbnumber",
        observed_at=observed_at,
    )

    bundle = UserBundle(
        identity=identity,
        evidence=tuple(evidence_list),
        profile=profile,
    )

    has_data = bool(evidence_list or not profile.is_empty or name)
    provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FOUND if has_data else ProviderStatus.NOT_FOUND,
        evidence=tuple(evidence_list),
        checked_at=observed_at,
        correlation_id=str(item.get("_id") or ""),
        profile=profile,
        name=name,
    )

    return EnrichedUser(
        bundle=bundle,
        provider_result=provider_result,
    )


@dataclass(frozen=True, slots=True)
class FBNumberScansSyncResult:
    total_count: int
    fetched_count: int
    imported_count: int
    skipped_count: int
    items: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def fetch_fbnumber_scans(
    *,
    api_token: str,
    page_number: int = 1,
    page_size: int = 100,
    filter_query: str = "",
    sort: str = "scanAt",
    order: int = -1,
    api_url: str = DEFAULT_FBNUMBER_SCANS_URL,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Call the FBNumber /v1/scans API endpoint and return the JSON response."""
    clean_token = api_token.strip().removeprefix("Bearer ").strip()
    if not clean_token:
        raise ValueError("FBNumber API Token is required.")

    url = api_url.strip()
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    params: dict[str, Any] = {
        "pageNumber": max(1, page_number),
        "pageSize": max(1, page_size),
        "sort": sort or "scanAt",
        "order": order,
    }
    if filter_query:
        params["filter"] = filter_query

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(url, headers=headers, params=params)

    if response.status_code == 401 or response.status_code == 403:
        raise PermissionError(f"FBNumber Authentication failed (HTTP {response.status_code}). Token không hợp lệ hoặc đã hết hạn.")
    if response.status_code != 200:
        raise RuntimeError(f"FBNumber API error (HTTP {response.status_code}): {response.text[:300]}")

    try:
        data = response.json()
    except Exception as err:
        raise ValueError("FBNumber API did not return valid JSON.") from err

    return data


def sync_scans_data_to_repository(
    scans_data: list[dict[str, Any]],
    repository: Any,
    *,
    default_country_code: str = "84",
) -> FBNumberScansSyncResult:
    """Import and upsert scan items into the Postgres database repository."""
    imported_count = 0
    skipped_count = 0
    errors: list[str] = []
    processed_items: list[dict[str, Any]] = []

    now = datetime.now(UTC)

    for item in scans_data:
        try:
            enriched = import_scan_item(
                item,
                default_country_code=default_country_code,
                fallback_time=now,
            )
            if enriched is None or not enriched.bundle.identity.is_usable:
                skipped_count += 1
                continue

            user_id = repository.save_enriched_user(enriched)
            imported_count += 1

            # Format for response
            phones = [ev.normalized_phone for ev in enriched.bundle.evidence]
            processed_items.append({
                "user_id": user_id,
                "uid": enriched.bundle.identity.uid,
                "username": enriched.bundle.identity.username,
                "name": enriched.bundle.identity.name,
                "profile_url": enriched.bundle.identity.profile_url,
                "phone_1": phones[0] if len(phones) > 0 else None,
                "phone_2": phones[1] if len(phones) > 1 else None,
                "address": enriched.bundle.profile.address,
                "gender": enriched.bundle.profile.gender,
                "birthday": enriched.bundle.profile.birth_date,
                "scan_at": item.get("scanAt"),
            })
        except DatabaseIdentityConflict as err:
            logger.warning("Identity conflict for item %s: %s", item.get("_id"), err)
            skipped_count += 1
        except Exception as err:
            logger.error("Error saving scan item %s: %s", item.get("_id"), err)
            errors.append(f"Item {item.get('_id')}: {str(err)}")
            skipped_count += 1

    return FBNumberScansSyncResult(
        total_count=len(scans_data),
        fetched_count=len(scans_data),
        imported_count=imported_count,
        skipped_count=skipped_count,
        items=processed_items,
        errors=errors,
    )
