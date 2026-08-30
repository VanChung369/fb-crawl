from __future__ import annotations

import base64

import pytest

from fb_crawl.core.exceptions import ConfigurationError
from fb_crawl.licenses.config import load_license_keyring


def _encoded(character: bytes) -> str:
    return base64.b64encode(character * 32).decode("ascii")


def test_license_keyring_loads_versioned_secrets_and_active_version() -> None:
    ring = load_license_keyring(
        {
            "LEAD_FINDER_LICENSE_HMAC_KEYS": (
                f"1:{_encoded(b'a')},2:{_encoded(b'b')}"
            ),
            "LEAD_FINDER_LICENSE_HMAC_ACTIVE_VERSION": "2",
        }
    )

    assert ring.active_version == 2
    assert ring.secrets == {1: b"a" * 32, 2: b"b" * 32}
    assert _encoded(b"a") not in repr(ring)


def test_license_keyring_requires_active_version_to_exist() -> None:
    with pytest.raises(ConfigurationError, match="active license HMAC version"):
        load_license_keyring(
            {
                "LEAD_FINDER_LICENSE_HMAC_KEYS": f"1:{_encoded(b'a')}",
                "LEAD_FINDER_LICENSE_HMAC_ACTIVE_VERSION": "2",
            }
        )


@pytest.mark.parametrize(
    "raw",
    ["", "1:not-base64", f"0:{_encoded(b'a')}", f"1:{base64.b64encode(b'short').decode()}"],
)
def test_license_keyring_rejects_missing_malformed_or_short_secrets(raw: str) -> None:
    with pytest.raises(ConfigurationError):
        load_license_keyring(
            {
                "LEAD_FINDER_LICENSE_HMAC_KEYS": raw,
                "LEAD_FINDER_LICENSE_HMAC_ACTIVE_VERSION": "1",
            }
        )
