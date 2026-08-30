from __future__ import annotations

from fb_crawl.auth.passwords import PasswordHasher


def test_password_hash_is_argon2id_and_never_contains_plaintext() -> None:
    hasher = PasswordHasher()
    password = "correct horse battery staple"

    encoded = hasher.hash(password)

    assert encoded.startswith("$argon2id$")
    assert "correct horse" not in encoded
    assert hasher.verify(encoded, password) is True
    assert hasher.verify(encoded, "wrong password") is False


def test_password_verification_rejects_malformed_hash_without_raising() -> None:
    assert PasswordHasher().verify("not-an-argon-hash", "password") is False
