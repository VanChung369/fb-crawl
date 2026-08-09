from __future__ import annotations

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    UserBundle,
)


class IdentityConflictError(ValueError):
    pass


CONFIDENCE_RANK = {
    "unknown": 0,
    "weak_pattern": 1,
    "strong_pattern": 2,
    "provider": 3,
    "profile_field": 4,
}


def _prefer_profile(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    left_numeric = "/profile.php" in left.casefold()
    right_numeric = "/profile.php" in right.casefold()
    if left_numeric != right_numeric:
        return right if left_numeric else left
    return left


def _merge_identity(
    left: FacebookIdentity,
    right: FacebookIdentity,
) -> FacebookIdentity:
    if left.uid and right.uid and left.uid != right.uid:
        raise IdentityConflictError(
            f"Conflicting Facebook UIDs: {left.uid} and {right.uid}."
        )
    return FacebookIdentity(
        uid=left.uid or right.uid,
        username=left.username or right.username,
        name=left.name or right.name,
        profile_url=_prefer_profile(left.profile_url, right.profile_url),
    )


def _stronger(left: PhoneEvidence, right: PhoneEvidence) -> PhoneEvidence:
    left_rank = CONFIDENCE_RANK.get(left.confidence, 0)
    right_rank = CONFIDENCE_RANK.get(right.confidence, 0)
    preferred = right if right_rank > left_rank else left
    captured_at = max(
        (value for value in (left.captured_at, right.captured_at) if value),
        default=None,
    )
    return PhoneEvidence(
        phone_number=preferred.phone_number,
        normalized_phone=preferred.normalized_phone,
        source=preferred.source,
        source_url=preferred.source_url,
        captured_at=captured_at,
        confidence=preferred.confidence,
        provider=preferred.provider,
        correlation_id=preferred.correlation_id or right.correlation_id,
    )


def merge_evidence(*groups: tuple[PhoneEvidence, ...]) -> tuple[PhoneEvidence, ...]:
    merged: dict[tuple[str, str, str, str], PhoneEvidence] = {}
    for group in groups:
        for item in group:
            current = merged.get(item.dedupe_key)
            merged[item.dedupe_key] = (
                item if current is None else _stronger(current, item)
            )
    return tuple(merged.values())


def merge_bundles(bundles: tuple[UserBundle, ...]) -> tuple[UserBundle, ...]:
    merged: list[UserBundle] = []
    for incoming in bundles:
        aliases = set(incoming.identity.aliases)
        matches = [
            index
            for index, existing in enumerate(merged)
            if aliases.intersection(existing.identity.aliases)
        ]
        if not matches:
            merged.append(incoming)
            continue

        first = matches[0]
        combined = incoming
        for index in matches:
            existing = merged[index]
            combined = UserBundle(
                identity=_merge_identity(existing.identity, combined.identity),
                evidence=merge_evidence(existing.evidence, combined.evidence),
            )
        for index in reversed(matches[1:]):
            del merged[index]
        merged[first] = combined

    return tuple(merged)

