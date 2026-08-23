"""Multi-session pool and rotation manager for Facebook accounts."""

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from fb_crawl.core.proxy_pool import ProxyPool


class SessionStatus(str, enum.Enum):
    HEALTHY = "healthy"
    COOLDOWN = "cooldown"
    EXPIRED = "expired"
    CHECKPOINT = "checkpoint"
    BLOCKED = "blocked"


@dataclass
class ManagedSession:
    path: Path
    proxy: str | None = None
    status: SessionStatus = SessionStatus.HEALTHY
    cooldown_until: float = 0.0
    failure_count: int = 0
    success_count: int = 0

    @property
    def is_available(self) -> bool:
        if self.status in {SessionStatus.EXPIRED, SessionStatus.CHECKPOINT, SessionStatus.BLOCKED}:
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
    ) -> None:
        self._sessions: list[ManagedSession] = []
        self._index = 0
        self._default_cooldown = default_cooldown_seconds
        self._proxy_mapping = proxy_mapping or {}
        self._proxy_pool = proxy_pool

        paths: list[Path] = []
        if sessions_dir and Path(sessions_dir).is_dir():
            paths.extend(sorted(Path(sessions_dir).glob("*.json")))
        if session_paths:
            for p in session_paths:
                resolved = Path(p)
                if resolved not in paths:
                    paths.append(resolved)

        for path in paths:
            proxy = self._proxy_mapping.get(path.name) or self._proxy_mapping.get(str(path))
            self._sessions.append(ManagedSession(path=path, proxy=proxy))

    @property
    def total_count(self) -> int:
        return len(self._sessions)

    @property
    def available_sessions(self) -> list[ManagedSession]:
        return [s for s in self._sessions if s.is_available]

    def add_session(self, path: Path, proxy: str | None = None) -> ManagedSession:
        resolved = Path(path)
        for s in self._sessions:
            if s.path == resolved:
                return s
        managed = ManagedSession(path=resolved, proxy=proxy)
        self._sessions.append(managed)
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
            if s.path == Path(path):
                s.status = SessionStatus.HEALTHY
                s.success_count += 1
                s.failure_count = 0
                break

    def mark_rate_limited(self, path: Path, cooldown_seconds: float | None = None) -> None:
        cooldown = cooldown_seconds if cooldown_seconds is not None else self._default_cooldown
        for s in self._sessions:
            if s.path == Path(path):
                s.status = SessionStatus.COOLDOWN
                s.cooldown_until = time.monotonic() + cooldown
                s.failure_count += 1
                break

    def mark_invalid(self, path: Path, status: SessionStatus = SessionStatus.EXPIRED) -> None:
        for s in self._sessions:
            if s.path == Path(path):
                s.status = status
                s.failure_count += 1
                break
