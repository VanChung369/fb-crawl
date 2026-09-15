from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from fb_crawl.licenses.config import LicenseKeyRing


@dataclass(frozen=True, slots=True)
class GeneratedLicense:
    plaintext: str = ""
    digest: str = ""
    masked: str = ""
    key_version: int = 0


class LicenseKeyService:
    def __init__(self, keyring: LicenseKeyRing) -> None:
        self._keyring = keyring

    def generate(self) -> GeneratedLicense:
        encoded = base64.b32encode(secrets.token_bytes(24)).decode("ascii").rstrip("=")
        groups = "-".join(
            encoded[index : index + 4]
            for index in range(0, len(encoded), 4)
        )
        plaintext = f"LF-{groups}"
        return GeneratedLicense(
            plaintext=plaintext,
            digest=self.digest(plaintext),
            masked=f"LF-****-****-{plaintext[-4:]}",
            key_version=self._keyring.active_version,
        )

    def digest(self, plaintext: str) -> str:
        return self._digest_for_version(
            plaintext, self._keyring.active_version
        )

    def _cipher(self, version: int) -> Fernet:
        # Domain separation keeps the encryption key distinct from redemption HMACs.
        derived = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None,
            info=b"lead-finder/license-key-encryption/v1",
        ).derive(self._keyring.secrets[version])
        return Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, plaintext: str, version: int) -> str:
        return self._cipher(version).encrypt(plaintext.encode("ascii")).decode("ascii")

    def decrypt(self, encrypted: str, version: int) -> str:
        return self._cipher(version).decrypt(encrypted.encode("ascii")).decode("ascii")

    def candidate_digests(self, plaintext: str) -> dict[int, str]:
        return {
            version: self._digest_for_version(plaintext, version)
            for version in self._keyring.secrets
        }

    def _digest_for_version(self, plaintext: str, version: int) -> str:
        normalized = _normalize_plaintext(plaintext)
        return hmac.new(
            self._keyring.secrets[version],
            normalized.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()


def _normalize_plaintext(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("license key must be a string")
    normalized = "".join(
        character for character in value.upper() if character not in {"-", " "}
    )
    if (
        not normalized.startswith("LF")
        or len(normalized) < 10
        or not normalized.isalnum()
        or not normalized.isascii()
    ):
        raise ValueError("license key format is invalid")
    return normalized
