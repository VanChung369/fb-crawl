from __future__ import annotations

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type


class PasswordHasher:
    """Small Argon2id boundary with stable production parameters."""

    def __init__(self) -> None:
        self._hasher = Argon2PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    def hash(self, password: str) -> str:
        if not isinstance(password, str):
            raise TypeError("password must be a string")
        return self._hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        if not isinstance(encoded, str) or not isinstance(password, str):
            return False
        try:
            return self._hasher.verify(encoded, password)
        except (InvalidHashError, VerificationError):
            return False

    def needs_rehash(self, encoded: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(encoded)
        except InvalidHashError:
            return True
