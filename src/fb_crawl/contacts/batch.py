from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fb_crawl.accounts.models import Account, Device
from fb_crawl.contacts.models import (
    LookupOutcome,
    LookupScanContext,
    LookupSourceType,
)
from fb_crawl.contacts.service import (
    ContactLookupRequest,
    ContactLookupResult,
    ContactLookupService,
)
from fb_crawl.core.exceptions import ValidationError


@dataclass(frozen=True, slots=True)
class BatchContactItem:
    request: ContactLookupRequest
    source_type: LookupSourceType | str
    source_url: str

    def __post_init__(self) -> None:
        try:
            source_type = LookupSourceType(str(self.source_type))
        except ValueError as error:
            raise ValidationError("Invalid batch contact source type.") from error
        if source_type is LookupSourceType.PROFILE:
            raise ValidationError("Invalid batch contact source type.")
        context = LookupScanContext(
            mode="manual_loaded",
            source_type=source_type,
            source_url=self.source_url,
        )
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_url", context.source_url)


@dataclass(frozen=True, slots=True)
class BatchContactResult:
    index: int
    duplicate_of: int | None
    result: ContactLookupResult


@dataclass(frozen=True, slots=True)
class BatchContactLookupResult:
    items: tuple[BatchContactResult, ...]
    detected_count: int
    unique_count: int
    processed_count: int
    found_count: int
    quota_exceeded_count: int


class BatchContactLookupService:
    def __init__(self, lookup_service: ContactLookupService) -> None:
        self.lookup_service = lookup_service

    def lookup(
        self,
        account: Account,
        device: Device,
        items: tuple[BatchContactItem, ...],
        now: datetime,
    ) -> BatchContactLookupResult:
        if not 1 <= len(items) <= 1_000:
            raise ValidationError("Batch contact count must be from 1 to 1000.")
        seen: dict[str, tuple[int, ContactLookupResult]] = {}
        results: list[BatchContactResult] = []
        found_count = 0
        quota_exceeded_count = 0
        for index, item in enumerate(items):
            identity = item.request.identity
            key = (
                f"uid:{identity.uid}"
                if identity.uid
                else f"username:{identity.username.casefold()}"
            )
            duplicate = seen.get(key)
            if duplicate is not None:
                first_index, first_result = duplicate
                results.append(BatchContactResult(index, first_index, first_result))
                continue
            result = self.lookup_service.lookup(
                account,
                device,
                item.request,
                now,
                force_refresh=False,
                scan_context=LookupScanContext(
                    mode="manual_loaded",
                    source_type=item.source_type,
                    source_url=item.source_url,
                ),
            )
            seen[key] = (index, result)
            results.append(BatchContactResult(index, None, result))
            found_count += result.state is LookupOutcome.FOUND
            quota_exceeded_count += (
                result.state is LookupOutcome.QUOTA_EXCEEDED
            )
        return BatchContactLookupResult(
            items=tuple(results),
            detected_count=len(items),
            unique_count=len(seen),
            processed_count=len(seen),
            found_count=found_count,
            quota_exceeded_count=quota_exceeded_count,
        )
