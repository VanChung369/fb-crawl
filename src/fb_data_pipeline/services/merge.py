from __future__ import annotations

from datetime import datetime

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProfileData,
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


def _profile_value(
    left: str,
    left_time: datetime | None,
    right: str,
    right_time: datetime | None,
) -> tuple[str, bool]:
    if not right:
        return left, False
    if not left:
        return right, True
    if right_time is not None and left_time is None:
        return right, True
    if left_time is not None and right_time is None:
        return left, False
    if right_time is not None and left_time is not None:
        return (right, True) if right_time >= left_time else (left, False)
    return left, False


def merge_profiles(left: ProfileData, right: ProfileData) -> ProfileData:
    if left.is_empty:
        return right
    if right.is_empty:
        return left

    address, address_from_right = _profile_value(
        left.address,
        left.observed_at,
        right.address,
        right.observed_at,
    )
    birth_date, birth_date_from_right = _profile_value(
        left.birth_date,
        left.observed_at,
        right.birth_date,
        right.observed_at,
    )
    gender, gender_from_right = _profile_value(
        left.gender,
        left.observed_at,
        right.gender,
        right.observed_at,
    )
    accepted_right = any(
        (address_from_right, birth_date_from_right, gender_from_right)
    )
    observed_at = max(
        (
            value
            for value in (left.observed_at, right.observed_at)
            if value is not None
        ),
        default=None,
    )
    return ProfileData(
        address=address,
        birth_date=birth_date,
        gender=gender,
        source_url=(
            right.source_url
            if accepted_right and right.source_url
            else left.source_url
        ),
        observed_at=observed_at,
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
                profile=merge_profiles(existing.profile, combined.profile),
            )
        for index in reversed(matches[1:]):
            del merged[index]
        merged[first] = combined

    return tuple(merged)
