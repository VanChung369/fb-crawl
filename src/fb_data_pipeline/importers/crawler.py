from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse

from fb_crawl.core.models import (
    ContactKind,
    PageRecord,
    ScrapeResult,
    UserRecord,
)
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    UserBundle,
)
from fb_data_pipeline.core.phone import InvalidPhoneNumber, normalize_phone
from fb_data_pipeline.services.merge import merge_evidence


@dataclass(frozen=True, slots=True)
class CrawlerImportBatch:
    bundles: tuple[UserBundle, ...]
    records_read: int
    records_skipped: int
    invalid_phones: int


def _timestamp(value: str | None) -> datetime | None:
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


def _username(profile_url: str, user_id: str = "") -> str:
    if user_id and not user_id.isdigit():
        return user_id

    parsed = urlparse(profile_url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) != 1 or parts[0].casefold() == "profile.php":
        return ""
    return parts[0]


def _facebook_source(value: str, fallback: str) -> str:
    source = str(value or fallback).strip()
    if source.casefold().startswith("facebook:"):
        return source
    return f"facebook:{source}"


def _confidence(source: str, fallback: str = "strong_pattern") -> str:
    normalized = source.casefold()
    if any(label in normalized for label in ("profile", "contact", "phone")):
        return "profile_field"
    return fallback


def _phone_evidence(
    *,
    phone_number: str,
    source: str,
    source_url: str,
    captured_at: datetime | None,
    confidence: str,
    default_country_code: str,
) -> PhoneEvidence:
    return PhoneEvidence(
        phone_number=phone_number,
        normalized_phone=normalize_phone(
            phone_number,
            default_country_code=default_country_code,
        ),
        source=_facebook_source(source, "crawler"),
        source_url=source_url,
        captured_at=captured_at,
        confidence=confidence,
    )


def import_user_record(
    record: UserRecord,
    *,
    default_country_code: str = "84",
) -> tuple[UserBundle, int]:
    user_id = str(record.user_id or "").strip()
    identity = FacebookIdentity(
        uid=user_id if user_id.isdigit() else "",
        username=(
            record.username
            or _username(record.profile_url, user_id)
        ),
        name=record.name or "",
        profile_url=record.profile_url,
    )
    evidence: list[PhoneEvidence] = []
    invalid_phones = 0
    precise_numbers: set[str] = set()

    for item in record.phone_evidence:
        try:
            converted = _phone_evidence(
                phone_number=item.value,
                source=item.source,
                source_url=item.source_url or record.profile_url,
                captured_at=_timestamp(item.captured_at),
                confidence=item.confidence,
                default_country_code=default_country_code,
            )
        except InvalidPhoneNumber:
            invalid_phones += 1
            continue
        precise_numbers.add(converted.normalized_phone)
        evidence.append(converted)

    captured_at = _timestamp(
        record.last_enriched_at or record.last_seen or record.first_seen
    )
    for index, number in enumerate(record.phone_numbers):
        try:
            normalized = normalize_phone(
                number,
                default_country_code=default_country_code,
            )
        except InvalidPhoneNumber:
            invalid_phones += 1
            continue
        if normalized in precise_numbers:
            continue

        if len(record.phone_sources) == len(record.phone_numbers):
            sources = (record.phone_sources[index],)
        elif len(record.phone_numbers) == 1 and record.phone_sources:
            sources = record.phone_sources
        else:
            sources = (record.source,)
        evidence.extend(
            _phone_evidence(
                phone_number=number,
                source=source,
                source_url=record.source_url or record.profile_url,
                captured_at=captured_at,
                confidence=_confidence(source, "strong_pattern"),
                default_country_code=default_country_code,
            )
            for source in sources
        )

    return (
        UserBundle(
            identity=identity,
            evidence=merge_evidence(tuple(evidence)),
        ),
        invalid_phones,
    )


def import_page_record(
    record: PageRecord,
    *,
    default_country_code: str = "84",
) -> tuple[UserBundle, int]:
    uid = str(record.uid or "").strip()
    identity = FacebookIdentity(
        uid=uid if uid.isdigit() else "",
        username=_username(record.canonical_url, uid),
        name=record.page_name or "",
        profile_url=record.canonical_url,
    )
    evidence: list[PhoneEvidence] = []
    invalid_phones = 0

    for contact in record.contacts:
        if contact.kind is not ContactKind.PHONE:
            continue
        sources = contact.sources or (record.discovery_source,)
        for source in sources:
            try:
                evidence.append(
                    _phone_evidence(
                        phone_number=contact.value,
                        source=source,
                        source_url=record.canonical_url,
                        captured_at=None,
                        confidence=_confidence(source, "strong_pattern"),
                        default_country_code=default_country_code,
                    )
                )
            except InvalidPhoneNumber:
                invalid_phones += 1
                break

    return (
        UserBundle(
            identity=identity,
            evidence=merge_evidence(tuple(evidence)),
        ),
        invalid_phones,
    )


def import_crawler_records(
    records: Iterable[UserRecord | PageRecord],
    *,
    default_country_code: str = "84",
) -> CrawlerImportBatch:
    bundles: list[UserBundle] = []
    records_read = 0
    records_skipped = 0
    invalid_phones = 0

    for record in records:
        records_read += 1
        if isinstance(record, UserRecord):
            bundle, invalid = import_user_record(
                record,
                default_country_code=default_country_code,
            )
        elif isinstance(record, PageRecord):
            bundle, invalid = import_page_record(
                record,
                default_country_code=default_country_code,
            )
        else:
            raise TypeError(
                f"Unsupported crawler record: {type(record).__name__}."
            )

        invalid_phones += invalid
        if not bundle.identity.is_usable:
            records_skipped += 1
            continue
        bundles.append(bundle)

    return CrawlerImportBatch(
        bundles=tuple(bundles),
        records_read=records_read,
        records_skipped=records_skipped,
        invalid_phones=invalid_phones,
    )


def import_scrape_result(
    result: ScrapeResult[UserRecord] | ScrapeResult[PageRecord],
    *,
    default_country_code: str = "84",
) -> CrawlerImportBatch:
    return import_crawler_records(
        result.records,
        default_country_code=default_country_code,
    )

