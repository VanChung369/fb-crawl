from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    FbCrawlError,
    IdentityResolutionError,
    RateLimitError,
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

    if status in {"interrupted", "running"}:
        return True

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


def _running(row: dict[str, str]) -> None:
    row["identity_status"] = "running"
    row["identity_source"] = "facebook:profile"
    row["identity_error_code"] = ""
    row["identity_error_message"] = ""


def _interrupted(
    row: dict[str, str],
    *,
    error: FbCrawlError | None = None,
) -> None:
    row["identity_status"] = "interrupted"
    row["identity_source"] = "facebook:profile"
    row["identity_error_code"] = error.code if error is not None else "interrupted"
    row["identity_error_message"] = (
        error.safe_message
        if error is not None
        else "Identity repair was interrupted safely."
    )


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
        jitter_func: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._session = session
        self._resolver = resolver
        self._sleep = sleep_func
        self._jitter = jitter_func

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
        max_retries: int = 2,
        retry_backoff_seconds: float = 5.0,
        retry_jitter_seconds: float = 1.0,
        progress_func: Callable[[IdentityRepairResult], None] | None = None,
    ) -> IdentityRepairResult:
        if limit <= 0:
            raise ValueError("repair limit must be greater than 0")

        if delay_seconds < 0:
            raise ValueError(
                "repair delay_seconds must be greater than or equal to 0"
            )

        if max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to 0")

        if retry_backoff_seconds < 0 or retry_jitter_seconds < 0:
            raise ValueError(
                "retry backoff and jitter must be greater than or equal to 0"
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
        attempted = 0
        retried = 0
        rate_limited = 0
        session_failed = 0
        interrupted = 0

        def result() -> IdentityRepairResult:
            completed = repaired + verified + failed
            return IdentityRepairResult(
                fieldnames=tuple(fieldnames),
                rows=tuple(dict(row) for row in rows),
                stats=IdentityRepairStats(
                    rows=len(rows),
                    eligible=len(eligible),
                    attempted=attempted,
                    repaired=repaired,
                    verified=verified,
                    failed=failed,
                    skipped=len(rows) - len(eligible),
                    pending=max(0, len(eligible) - completed),
                    retried=retried,
                    rate_limited=rate_limited,
                    session_failed=session_failed,
                    interrupted=interrupted,
                ),
            )

        def progress() -> None:
            if progress_func is not None:
                progress_func(result())

        if selected:
            try:
                self._session.ensure_authenticated(browser)
            except KeyboardInterrupt:
                interrupted = 1
                progress()
                return result()
            except SessionError:
                session_failed = 1
                progress()
                return result()

        for position, index in enumerate(selected):
            row = rows[index]
            target = _repair_target(row)

            if target is None:
                continue

            _running(row)
            attempted += 1
            progress()
            stop = False

            for retry_index in range(max_retries + 1):
                try:
                    self._session.assert_authenticated(browser)
                    status = _apply_identity(
                        row,
                        self._resolver.resolve(browser, target),
                    )
                except KeyboardInterrupt:
                    _interrupted(row)
                    interrupted = 1
                    stop = True
                    break
                except SessionError as error:
                    _interrupted(row, error=error)
                    session_failed = 1
                    stop = True
                    break
                except FbCrawlError as error:
                    rate_limited += isinstance(error, RateLimitError)
                    retryable = isinstance(
                        error,
                        (BrowserNavigationError, IdentityResolutionError),
                    )

                    if retryable and retry_index < max_retries:
                        retried += 1
                        backoff = min(
                            retry_backoff_seconds * (2**retry_index),
                            300.0,
                        )

                        try:
                            self._sleep(
                                backoff
                                + self._jitter(0.0, retry_jitter_seconds)
                            )
                        except KeyboardInterrupt:
                            _interrupted(row)
                            interrupted = 1
                            stop = True
                            break

                        continue

                    _failure(row, error)
                    failed += 1
                    break
                except Exception:
                    _failure(
                        row,
                        IdentityResolutionError(
                            "Authenticated profile identity resolution failed.",
                            target=target.profile_url,
                        ),
                    )
                    failed += 1
                    break
                else:
                    repaired += status == "repaired"
                    verified += status == "verified"
                    break

            progress()

            if stop:
                break

            if position + 1 < len(selected) and delay_seconds:
                try:
                    self._sleep(delay_seconds)
                except KeyboardInterrupt:
                    interrupted = 1
                    progress()
                    break

        return result()
