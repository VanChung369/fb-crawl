from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from fb_crawl.accounts.models import DeviceStatus
from fb_crawl.contacts.models import (
    LookupOutcome,
    LookupScanContext,
    LookupSourceType,
)
from fb_crawl.contacts.service import ContactLookupRequest
from fb_crawl.core.models import AuthenticatedAction, UserRecord


class ProductJobResultSink:
    def __init__(self, repository, accounts, contacts) -> None:
        self.repository = repository
        self.accounts = accounts
        self.contacts = contacts

    def record(
        self,
        crawl_job_id: UUID,
        action: AuthenticatedAction,
        records: tuple[UserRecord, ...],
        now: datetime,
    ) -> None:
        owner = self.repository.owner_for_child(crawl_job_id)
        if owner is None:
            return
        account = self.accounts.get_account(owner.account_id)
        devices = self.accounts.list_devices(owner.account_id)
        device = next(
            (item for item in devices if item.status is DeviceStatus.ACTIVE),
            None,
        )
        if account is None or device is None:
            return

        seen: set[str] = set()
        processed = found = not_found = quota = 0
        source_type = (
            LookupSourceType.MEMBER
            if action is AuthenticatedAction.MEMBERS
            else LookupSourceType.COMMENT_AUTHOR
        )
        for record in records:
            if processed >= owner.max_identities:
                break
            uid = record.user_id.strip() if re.fullmatch(r"[1-9]\d{4,19}", record.user_id.strip()) else ""
            username = (record.username or "").strip().casefold()
            key = f"uid:{uid}" if uid else f"username:{username}"
            if not uid and not username or key in seen:
                continue
            seen.add(key)
            result = self.contacts.lookup(
                account,
                device,
                ContactLookupRequest(
                    facebook_uid=uid,
                    username=username,
                    name=record.name or "",
                    profile_url=record.profile_url,
                ),
                now,
                force_refresh=False,
                scan_context=LookupScanContext(
                    mode="automatic",
                    source_type=source_type,
                    source_url=record.source_url,
                    product_crawl_job_id=owner.product_job_id,
                ),
            )
            processed += 1
            found += result.state is LookupOutcome.FOUND
            not_found += result.state is LookupOutcome.NOT_FOUND
            quota += result.state is LookupOutcome.QUOTA_EXCEEDED
        self.repository.add_result_counts(
            owner.product_job_id,
            owner.account_id,
            processed=processed,
            found=found,
            not_found=not_found,
            quota_exceeded=quota,
            now=now,
        )
