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
    assert len(pool.available_sessions) == 2

    # Check proxy mapping
    acc1 = next(s for s in pool.available_sessions if s.path.name == "acc1.json")
    assert acc1.proxy == "http://127.0.0.1:8080"


def test_session_pool_round_robin_rotation(tmp_path: Path) -> None:
    s1 = tmp_path / "acc1.json"
    s2 = tmp_path / "acc2.json"
    pool = SessionPool([s1, s2])

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
