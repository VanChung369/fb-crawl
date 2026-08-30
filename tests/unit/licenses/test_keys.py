from __future__ import annotations

from fb_crawl.licenses.config import LicenseKeyRing
from fb_crawl.licenses.keys import LicenseKeyService


def test_generated_license_plaintext_is_high_entropy_human_format_and_hash_only() -> None:
    keys = LicenseKeyService(
        LicenseKeyRing(active_version=2, secrets={1: b"a" * 32, 2: b"b" * 32})
    )

    first = keys.generate()
    second = keys.generate()

    assert first.plaintext.startswith("LF-")
    assert first.plaintext != second.plaintext
    assert len(first.plaintext.replace("-", "").removeprefix("LF")) >= 32
    assert first.plaintext not in first.digest
    assert first.key_version == 2
    assert first.masked.startswith("LF-****")
    assert first.masked.endswith(first.plaintext[-4:])
    assert keys.digest(first.plaintext) == first.digest


def test_license_digest_normalizes_case_spaces_and_hyphens() -> None:
    keys = LicenseKeyService(
        LicenseKeyRing(active_version=1, secrets={1: b"a" * 32})
    )
    generated = keys.generate()
    transcription = generated.plaintext.lower().replace("-", " ")

    assert keys.digest(transcription) == generated.digest


def test_candidate_digests_cover_active_and_historical_key_versions() -> None:
    keys = LicenseKeyService(
        LicenseKeyRing(active_version=2, secrets={1: b"a" * 32, 2: b"b" * 32})
    )

    candidates = keys.candidate_digests("LF-ABCD-EFGH")

    assert set(candidates) == {1, 2}
    assert candidates[1] != candidates[2]
