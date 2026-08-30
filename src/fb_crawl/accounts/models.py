from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AccountRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class AccountStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class DeviceStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    normalized_email: str
    display_email: str
    password_hash: str = field(repr=False)
    role: AccountRole
    status: AccountStatus
    email_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deletion_requested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Device:
    id: int
    account_id: int
    installation_id: UUID
    display_name: str
    status: DeviceStatus
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class AuthSession:
    id: UUID
    account_id: int
    device_id: int
    expires_at: datetime
    rotated_from_id: UUID | None
    revoked_at: datetime | None
    created_at: datetime
    last_used_at: datetime
    authenticated_at: datetime
