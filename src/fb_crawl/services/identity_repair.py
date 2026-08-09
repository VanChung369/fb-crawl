from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from fb_crawl.core.exceptions import (
    FbCrawlError,
    IdentityResolutionError,
    SessionError,
)
from fb_crawl.core.identity import is_suspicious_profile_name
from fb_crawl.core.models import (
    IdentityRepairResult,
    IdentityRepairStats,
    ProfileIdentity,
    UserRecord,
)
from fb_crawl.core.urls import profile_identity_url


class SessionPort(Protocol):
    def ensure_authenticated(self, browser) -> None: ...

    def assert_authenticated(self, browser) -> None: ...


class IdentityResolverPort(Protocol):
    def resolve(self, browser, record: UserRecord) -> ProfileIdentity: ...


def _clean(value: object) -> str:
    return str(value or "").strip()


def _repair_target(row: Mapping[str, str]) -> UserRecord | None:
    raw_user_id = _clean(row.get("user_id"))
    raw_username = _clean(row.get("username"))
    raw_profile_url = _clean(row.get("profile_url"))
    candidates = (
        raw_profile_url,
        f"https://www.facebook.com/{raw_username}" if raw_username else "",
        (
            f"https://www.facebook.com/profile.php?id={raw_user_id}"
            if raw_user_id.isdigit()
            else f"https://www.facebook.com/{raw_user_id}"
            if raw_user_id
            else ""
        ),
    )
    parsed = next(
        (
            identity
            for candidate in candidates
            if candidate and (identity := profile_identity_url(candidate))
        ),
        None,
    )

    if parsed is None:
        return None

    url_identity, profile_url = parsed
    user_id = raw_user_id or url_identity
    username = raw_username or (
        url_identity if not url_identity.isdigit() else ""
    )
    return UserRecord(
        user_id=user_id,
        name=_clean(row.get("name")) or None,
        username=username or None,
        profile_url=profile_url,
        source=_clean(row.get("source")) or "repair",
        source_url=_clean(row.get("source_url")) or profile_url,
    )


def _profile_url_is_inconsistent(row: Mapping[str, str]) -> bool:
    parsed = profile_identity_url(_clean(row.get("profile_url")))

    if parsed is None:
        return True

    url_identity, _ = parsed
    user_id = _clean(row.get("user_id"))
    username = _clean(row.get("username"))

    if user_id.isdigit() and url_identity.isdigit():
        return user_id != url_identity

    if username and not url_identity.isdigit():
        return username.casefold() != url_identity.casefold()

    return False


def needs_identity_repair(
    row: Mapping[str, str],
    *,
    force: bool = False,
    retry_failed: bool = False,
) -> bool:
    if _repair_target(row) is None:
        return False

    status = _clean(row.get("identity_status")).casefold()

    if force:
        return True

    if status == "failed":
        return retry_failed

    if status in {"verified", "repaired"}:
        return False

    return (
        not _clean(row.get("user_id")).isdigit()
        or is_suspicious_profile_name(_clean(row.get("name")))
        or not _clean(row.get("username"))
        or _profile_url_is_inconsistent(row)
    )


def _failure(row: dict[str, str], error: FbCrawlError) -> None:
    row["identity_status"] = "failed"
    row["identity_source"] = "facebook:profile"
    row["identity_error_code"] = error.code
    row["identity_error_message"] = error.safe_message


def _apply_identity(
    row: dict[str, str],
    identity: ProfileIdentity,
) -> str:
    previous = tuple(
        _clean(row.get(field))
        for field in ("user_id", "name", "username", "profile_url")
    )
    name = identity.name or _clean(row.get("name"))

    if not identity.user_id.isdigit() or is_suspicious_profile_name(name):
        raise IdentityResolutionError(
            "Authenticated profile identity resolution was incomplete.",
            target=identity.profile_url,
        )

    row.update(
        {
            "user_id": identity.user_id,
            "name": name,
            "username": identity.username or "",
            "profile_url": identity.profile_url,
            "identity_source": identity.source,
            "identity_error_code": "",
            "identity_error_message": "",
        }
    )
    current = tuple(
        row[field]
        for field in ("user_id", "name", "username", "profile_url")
    )
    status = "repaired" if current != previous else "verified"
    row["identity_status"] = status
    return status


class IdentityRepairService:
    def __init__(
        self,
        session: SessionPort,
        resolver: IdentityResolverPort,
        *,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session
        self._resolver = resolver
        self._sleep = sleep_func

    def run(
        self,
        fieldnames: Sequence[str],
        source_rows: Sequence[Mapping[str, str]],
        browser,
        *,
        force: bool = False,
        retry_failed: bool = False,
        limit: int = 20,
        delay_seconds: float = 3.0,
    ) -> IdentityRepairResult:
        if limit <= 0:
            raise ValueError("repair limit must be greater than 0")

        if delay_seconds < 0:
            raise ValueError(
                "repair delay_seconds must be greater than or equal to 0"
            )

        rows = [dict(row) for row in source_rows]
        eligible = [
            index
            for index, row in enumerate(rows)
            if needs_identity_repair(
                row,
                force=force,
                retry_failed=retry_failed,
            )
        ]
        selected = eligible[:limit]
        repaired = 0
        verified = 0
        failed = 0

        if selected:
            self._session.ensure_authenticated(browser)

        for position, index in enumerate(selected):
            row = rows[index]
            target = _repair_target(row)

            if target is None:
                continue

            self._session.assert_authenticated(browser)

            try:
                status = _apply_identity(
                    row,
                    self._resolver.resolve(browser, target),
                )
            except SessionError:
                raise
            except FbCrawlError as error:
                _failure(row, error)
                failed += 1
            except Exception:
                _failure(
                    row,
                    IdentityResolutionError(
                        "Authenticated profile identity resolution failed.",
                        target=target.profile_url,
                    ),
                )
                failed += 1
            else:
                repaired += status == "repaired"
                verified += status == "verified"

            if position + 1 < len(selected) and delay_seconds:
                self._sleep(delay_seconds)

        attempted = len(selected)
        return IdentityRepairResult(
            fieldnames=tuple(fieldnames),
            rows=tuple(rows),
            stats=IdentityRepairStats(
                rows=len(rows),
                eligible=len(eligible),
                attempted=attempted,
                repaired=repaired,
                verified=verified,
                failed=failed,
                skipped=len(rows) - len(eligible),
                pending=len(eligible) - attempted,
            ),
        )
