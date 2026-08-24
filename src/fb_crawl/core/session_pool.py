"""Multi-session pool and rotation manager for Facebook accounts."""

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fb_crawl.core.proxy_pool import ProxyPool


class SessionStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    COOLDOWN = "cooldown"
    EXPIRED = "expired"
    CHECKPOINT = "checkpoint"
    BLOCKED = "blocked"
    MANUAL_REVIEW = "manual_review"


SESSION_POOL_STATE_FILENAME = ".session-pool-state.json"


@dataclass
class ManagedSession:
    path: Path
    proxy: str | None = None
    status: SessionStatus = SessionStatus.UNKNOWN
    cooldown_until: float = 0.0
    failure_count: int = 0
    success_count: int = 0

    @property
    def is_available(self) -> bool:
        if self.status in {
            SessionStatus.UNKNOWN,
            SessionStatus.EXPIRED,
            SessionStatus.CHECKPOINT,
            SessionStatus.BLOCKED,
            SessionStatus.MANUAL_REVIEW,
        }:
            return False
        if self.status == SessionStatus.COOLDOWN and time.monotonic() < self.cooldown_until:
            return False
        return True


class SessionPool:
    """Manages multiple Facebook session files with rotation, health tracking, and proxy support."""

    def __init__(
        self,
        session_paths: list[Path] | None = None,
        *,
        sessions_dir: Path | None = None,
        proxy_mapping: Mapping[str, str] | None = None,
        proxy_pool: ProxyPool | None = None,
        default_cooldown_seconds: float = 3600.0,
        state_path: Path | None = None,
    ) -> None:
        self._sessions: list[ManagedSession] = []
        self._index = 0
        self._default_cooldown = default_cooldown_seconds
        self._proxy_mapping = proxy_mapping or {}
        self._proxy_pool = proxy_pool
        self._state_path = (
            state_path
            if state_path is not None
            else (
                Path(sessions_dir) / SESSION_POOL_STATE_FILENAME
                if sessions_dir is not None
                else None
            )
        )
        self._stored_state = self._load_state()

        paths: list[Path] = []
        if sessions_dir and Path(sessions_dir).is_dir():
            paths.extend(
                path
                for path in sorted(Path(sessions_dir).glob("*.json"))
                if path.name != SESSION_POOL_STATE_FILENAME
            )
        if session_paths:
            for p in session_paths:
                resolved = Path(p)
                if not any(_same_session_path(existing, resolved) for existing in paths):
                    paths.append(resolved)

        for path in paths:
            proxy = self._proxy_mapping.get(path.name) or self._proxy_mapping.get(str(path))
            managed = ManagedSession(path=path, proxy=proxy)
            self._apply_stored_state(managed)
            self._sessions.append(managed)

    @property
    def total_count(self) -> int:
        return len(self._sessions)

    @property
    def available_sessions(self) -> list[ManagedSession]:
        return [s for s in self._sessions if s.is_available]

    def add_session(self, path: Path, proxy: str | None = None) -> ManagedSession:
        resolved = Path(path)
        for s in self._sessions:
            if _same_session_path(s.path, resolved):
                if proxy is not None:
                    s.proxy = proxy if proxy.strip() else None
                    self._save_state()
                return s
        managed = ManagedSession(path=resolved, proxy=proxy)
        self._apply_stored_state(managed)
        if proxy is not None:
            managed.proxy = proxy if proxy.strip() else None
        self._sessions.append(managed)
        self._save_state()
        return managed

    def next_session(self) -> ManagedSession | None:
        """Return the next available session using round-robin rotation."""
        available = self.available_sessions
        if not available:
            return None

        # Round robin across available sessions
        self._index = (self._index + 1) % len(available)
        session = available[self._index]
        if self._proxy_pool and not session.proxy:
            proxy_entry = self._proxy_pool.get_proxy()
            if proxy_entry:
                session.proxy = proxy_entry.formatted_url
        return session

    def mark_success(self, path: Path) -> None:
        for s in self._sessions:
            if _same_session_path(s.path, Path(path)):
                s.status = SessionStatus.HEALTHY
                s.success_count += 1
                s.failure_count = 0
                s.cooldown_until = 0.0
                self._save_state()
                break

    def mark_rate_limited(self, path: Path, cooldown_seconds: float | None = None) -> None:
        cooldown = cooldown_seconds if cooldown_seconds is not None else self._default_cooldown
        for s in self._sessions:
            if _same_session_path(s.path, Path(path)):
                s.status = SessionStatus.COOLDOWN
                s.cooldown_until = time.monotonic() + cooldown
                s.failure_count += 1
                self._save_state()
                break

    def mark_invalid(self, path: Path, status: SessionStatus = SessionStatus.EXPIRED) -> None:
        for s in self._sessions:
            if _same_session_path(s.path, Path(path)):
                s.status = status
                s.failure_count += 1
                s.cooldown_until = 0.0
                self._save_state()
                break

    def remove_session(self, name_or_path: str | Path) -> bool:
        target_name = Path(name_or_path).name
        initial_len = len(self._sessions)
        self._sessions = [s for s in self._sessions if s.path.name != target_name]
        removed = len(self._sessions) < initial_len
        if removed:
            self._stored_state.pop(target_name, None)
            self._save_state()
        return removed

    def update_session(
        self,
        name_or_path: str | Path,
        *,
        proxy: str | None = None,
        status: SessionStatus | str | None = None,
    ) -> ManagedSession | None:
        target_name = Path(name_or_path).name
        for s in self._sessions:
            if s.path.name == target_name:
                if proxy is not None:
                    s.proxy = proxy if proxy.strip() else None
                if status is not None:
                    try:
                        s.status = SessionStatus(status)
                        if s.status != SessionStatus.COOLDOWN:
                            s.cooldown_until = 0.0
                    except ValueError:
                        pass
                self._save_state()
                return s
        return None

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if self._state_path is None or not self._state_path.is_file():
            return {}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        raw_sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(raw_sessions, dict):
            return {}
        return {
            str(name): state
            for name, state in raw_sessions.items()
            if isinstance(name, str) and isinstance(state, dict)
        }

    def _apply_stored_state(self, managed: ManagedSession) -> None:
        state = self._stored_state.get(managed.path.name)
        if not state:
            return
        proxy = state.get("proxy")
        if isinstance(proxy, str):
            managed.proxy = proxy or None
        status = state.get("status")
        if isinstance(status, str):
            try:
                managed.status = SessionStatus(status)
            except ValueError:
                pass
        managed.failure_count = _non_negative_int(state.get("failure_count"))
        managed.success_count = _non_negative_int(state.get("success_count"))
        cooldown_until_epoch = _optional_float(state.get("cooldown_until_epoch"))
        if managed.status == SessionStatus.COOLDOWN and cooldown_until_epoch is not None:
            remaining = max(0.0, cooldown_until_epoch - time.time())
            managed.cooldown_until = time.monotonic() + remaining

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        sessions: dict[str, dict[str, object]] = {}
        now = time.time()
        monotonic_now = time.monotonic()
        for session in self._sessions:
            cooldown_remaining = max(0.0, session.cooldown_until - monotonic_now)
            sessions[session.path.name] = {
                "proxy": session.proxy or "",
                "status": session.status.value,
                "failure_count": session.failure_count,
                "success_count": session.success_count,
                "cooldown_until_epoch": (
                    now + cooldown_remaining
                    if session.status == SessionStatus.COOLDOWN
                    else 0.0
                ),
            }
        payload = {"version": 1, "sessions": sessions}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(self._state_path)
            self._stored_state = sessions
        except OSError:
            return


def _same_session_path(left: Path, right: Path) -> bool:
    if left.name != right.name:
        return False
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return left == right


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
