from __future__ import annotations

import time
from pathlib import Path
from fb_crawl.adapters.browser.session_pool import (
    ManagedSession,
    SessionPool,
    SessionStatus,
)


def test_session_pool_loads_from_directory(tmp_path: Path) -> None:
    s1 = tmp_path / "acc1.json"
    s2 = tmp_path / "acc2.json"
    s1.write_text("[]", encoding="utf-8")
    s2.write_text("[]", encoding="utf-8")

    pool = SessionPool(
        sessions_dir=tmp_path,
        proxy_mapping={"acc1.json": "http://127.0.0.1:8080"},
    )
    assert pool.total_count == 2
    assert len(pool.available_sessions) == 0

    # Check proxy mapping
    acc1 = next(s for s in pool._sessions if s.path.name == "acc1.json")
    assert acc1.proxy == "http://127.0.0.1:8080"
    assert acc1.status is SessionStatus.UNKNOWN


def test_session_pool_does_not_mark_unchecked_files_as_healthy(
    tmp_path: Path,
) -> None:
    """Break caught: reopening the API paints every unmanaged cookie file green."""

    session_file = tmp_path / "acc1.json"
    session_file.write_text("[]", encoding="utf-8")

    pool = SessionPool(sessions_dir=tmp_path)

    assert pool.total_count == 1
    assert pool.available_sessions == []
    assert pool._sessions[0].status is SessionStatus.UNKNOWN


def test_session_pool_round_robin_rotation(tmp_path: Path) -> None:
    s1 = tmp_path / "acc1.json"
    s2 = tmp_path / "acc2.json"
    pool = SessionPool([s1, s2])
    pool.mark_success(s1)
    pool.mark_success(s2)

    # Round robin iterates through available sessions
    first = pool.next_session()
    second = pool.next_session()
    third = pool.next_session()

    assert {first.path, second.path} == {s1, s2}
    assert third.path == first.path


def test_session_pool_skips_cooldown_and_expired(tmp_path: Path) -> None:
    s1 = tmp_path / "acc1.json"
    s2 = tmp_path / "acc2.json"
    pool = SessionPool([s1, s2])
    pool.mark_success(s1)
    pool.mark_success(s2)

    pool.mark_rate_limited(s1, cooldown_seconds=600)
    assert len(pool.available_sessions) == 1
    assert pool.available_sessions[0].path == s2

    pool.mark_invalid(s2, SessionStatus.CHECKPOINT)
    assert len(pool.available_sessions) == 0
    assert pool.next_session() is None


def test_session_pool_mark_success_clears_failure(tmp_path: Path) -> None:
    s1 = tmp_path / "acc1.json"
    pool = SessionPool([s1])
    pool.mark_rate_limited(s1, cooldown_seconds=0)
    pool.mark_success(s1)

    assert pool.available_sessions[0].status == SessionStatus.HEALTHY
    assert pool.available_sessions[0].success_count == 1
    assert pool.available_sessions[0].failure_count == 0


def test_session_pool_deduplicates_relative_and_absolute_session_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Break caught: API checks/imports append the same session repeatedly."""

    monkeypatch.chdir(tmp_path)
    session_file = tmp_path / "runtime" / "sessions" / "acc1.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("[]", encoding="utf-8")
    pool = SessionPool(sessions_dir=Path("runtime/sessions"))

    managed = pool.add_session(session_file.resolve(), proxy="http://127.0.0.1:8080")

    assert pool.total_count == 1
    assert managed.path.name == "acc1.json"
    assert managed.proxy == "http://127.0.0.1:8080"


def test_session_pool_constructor_deduplicates_session_dir_and_explicit_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Break caught: bootstrap can double-load the same account under mixed paths."""

    monkeypatch.chdir(tmp_path)
    session_file = tmp_path / "runtime" / "sessions" / "acc1.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("[]", encoding="utf-8")

    pool = SessionPool(
        session_paths=[session_file.resolve()],
        sessions_dir=Path("runtime/sessions"),
    )

    assert pool.total_count == 1
    assert pool.available_sessions == []
    assert pool._sessions[0].path.name == "acc1.json"
    assert pool._sessions[0].status is SessionStatus.UNKNOWN


def test_session_pool_persists_status_counts_and_proxy_across_reloads(
    tmp_path: Path,
) -> None:
    """Break caught: checked sessions reset to all-green after reopening the UI/API."""

    session_file = tmp_path / "acc1.json"
    session_file.write_text("[]", encoding="utf-8")
    pool = SessionPool(sessions_dir=tmp_path)

    pool.update_session(
        "acc1.json",
        proxy="http://127.0.0.1:8080",
        status=SessionStatus.CHECKPOINT,
    )
    pool.mark_invalid(session_file, SessionStatus.CHECKPOINT)

    reloaded = SessionPool(sessions_dir=tmp_path)
    session = reloaded.available_sessions

    assert reloaded.total_count == 1
    assert session == []
    stored = reloaded._sessions[0]
    assert stored.status is SessionStatus.CHECKPOINT
    assert stored.proxy == "http://127.0.0.1:8080"
    assert stored.failure_count == 1


def test_session_pool_does_not_treat_state_file_as_cookie_session(
    tmp_path: Path,
) -> None:
    """Break caught: persistence metadata appears as a fake Facebook account."""

    session_file = tmp_path / "acc1.json"
    session_file.write_text("[]", encoding="utf-8")
    SessionPool(sessions_dir=tmp_path).mark_success(session_file)

    reloaded = SessionPool(sessions_dir=tmp_path)

    assert [session.path.name for session in reloaded._sessions] == ["acc1.json"]
