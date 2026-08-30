from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=2048)


class ResetPasswordRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    installation_id: UUID
    device_name: str = Field(min_length=1, max_length=128)
    transport: Literal["extension", "web"] = "extension"


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, min_length=16, max_length=2048)
    installation_id: UUID
    transport: Literal["extension", "web"] = "extension"


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int
    email: str
    verification_required: bool


class GenericAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool = True


class AuthTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str | None
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime


class AccountMeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    email: str
    role: Literal["user", "admin"]
    status: Literal["pending", "active", "suspended", "deleted"]
    email_verified_at: datetime | None


class DeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    installation_id: UUID
    display_name: str
    status: Literal["active", "revoked"]
    first_seen_at: datetime
    last_seen_at: datetime
    current: bool


class DeviceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeviceResponse]


class DeviceRevokedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["revoked"] = "revoked"
    device_id: int


class AccountDeletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["deleted"] = "deleted"
