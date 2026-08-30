from __future__ import annotations

from datetime import datetime

from fb_crawl.licenses.keys import LicenseKeyService
from fb_crawl.licenses.models import AdminAuditEvent, LicenseGrant, LicenseKey, Subscription
from fb_crawl.licenses.postgres import PostgresLicenseRepository
from fb_crawl.licenses.repository import InvalidLicenseKey, LicenseError


class LicenseService:
    def __init__(
        self,
        repository: PostgresLicenseRepository,
        key_service: LicenseKeyService,
    ) -> None:
        self.repository = repository
        self.key_service = key_service

    def create_key(
        self,
        grant: LicenseGrant,
        actor_account_id: int,
        now: datetime,
    ) -> tuple[LicenseKey, str]:
        generated = self.key_service.generate()
        key = self.repository.create_key(
            key_digest=generated.digest,
            key_version=generated.key_version,
            masked_key=generated.masked,
            grant=grant,
            created_by_account_id=actor_account_id,
            now=now,
        )
        return key, generated.plaintext

    def redeem(
        self, account_id: int, plaintext: str, now: datetime
    ) -> Subscription:
        try:
            candidates = self.key_service.candidate_digests(plaintext)
        except (TypeError, ValueError):
            raise InvalidLicenseKey("License key is invalid.") from None
        for digest in candidates.values():
            try:
                return self.repository.redeem(account_id, digest, now)
            except LicenseError:
                continue
        raise InvalidLicenseKey("License key is invalid.")

    def list_keys(
        self, *, limit: int = 100, cursor: int | None = None
    ) -> tuple[LicenseKey, ...]:
        return self.repository.list_keys(limit=limit, cursor=cursor)

    def revoke_key(
        self, key_id: int, actor_account_id: int, now: datetime
    ) -> LicenseKey:
        return self.repository.revoke_key(key_id, actor_account_id, now)

    def list_subscriptions(self, account_id: int) -> tuple[Subscription, ...]:
        return self.repository.list_subscriptions(account_id)

    def start_subscription_now(
        self,
        account_id: int,
        subscription_id: int,
        actor_account_id: int,
        now: datetime,
    ) -> tuple[Subscription, ...]:
        return self.repository.start_subscription_now(
            account_id, subscription_id, actor_account_id, now
        )

    def write_audit(
        self,
        *,
        actor_account_id: int,
        action: str,
        target_type: str,
        target_id: str,
        details: dict[str, object],
        now: datetime,
    ) -> None:
        self.repository.write_audit(
            actor_account_id=actor_account_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            now=now,
        )

    def list_audit_events(
        self, *, limit: int = 100, cursor: int | None = None
    ) -> tuple[AdminAuditEvent, ...]:
        return self.repository.list_audit_events(limit=limit, cursor=cursor)
