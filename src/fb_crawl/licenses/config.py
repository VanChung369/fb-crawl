from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from fb_crawl.core.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class LicenseKeyRing:
    active_version: int
    secrets: Mapping[int, bytes] = field(repr=False)

    def __post_init__(self) -> None:
        immutable = MappingProxyType(dict(self.secrets))
        if self.active_version not in immutable:
            raise ConfigurationError(
                "The active license HMAC version is not configured."
            )
        if any(
            isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
            or not isinstance(secret, bytes)
            or len(secret) < 32
            for version, secret in immutable.items()
        ):
            raise ConfigurationError(
                "License HMAC versions must be positive and secrets must decode to at least 32 bytes."
            )
        object.__setattr__(self, "secrets", immutable)


def load_license_keyring(env: Mapping[str, str]) -> LicenseKeyRing:
    if not isinstance(env, Mapping):
        raise ConfigurationError("License settings require an injected mapping.")
    raw_keys = env.get("LEAD_FINDER_LICENSE_HMAC_KEYS", "").strip()
    raw_active = env.get(
        "LEAD_FINDER_LICENSE_HMAC_ACTIVE_VERSION", ""
    ).strip()
    try:
        active_version = int(raw_active)
    except ValueError as error:
        raise ConfigurationError(
            "LEAD_FINDER_LICENSE_HMAC_ACTIVE_VERSION must be an integer."
        ) from error

    secrets: dict[int, bytes] = {}
    try:
        entries = [item.strip() for item in raw_keys.split(",") if item.strip()]
        if not entries:
            raise ValueError("missing keys")
        for entry in entries:
            raw_version, encoded = entry.split(":", 1)
            version = int(raw_version)
            if version in secrets:
                raise ValueError("duplicate version")
            secrets[version] = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ConfigurationError(
            "LEAD_FINDER_LICENSE_HMAC_KEYS must contain version:base64 entries."
        ) from error

    return LicenseKeyRing(active_version=active_version, secrets=secrets)
