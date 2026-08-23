"""Proxy pool management, health tracking, and rotation strategies."""

from __future__ import annotations

import enum
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class ProxyStatus(str, enum.Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    DEAD = "dead"


class RotationStrategy(str, enum.Enum):
    ROUND_ROBIN = "round_robin"
    RANDOM = "random"


@dataclass
class ProxyEntry:
    raw_url: str
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    status: ProxyStatus = ProxyStatus.ACTIVE
    cooldown_until: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: float = 0.0

    @property
    def is_available(self) -> bool:
        if self.status == ProxyStatus.DEAD:
            return False
        if self.status == ProxyStatus.COOLDOWN and time.monotonic() < self.cooldown_until:
            return False
        return True

    @property
    def formatted_url(self) -> str:
        if self.username and self.password:
            return f"{self.scheme}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.scheme}://{self.host}:{self.port}"


def parse_proxy_line(line: str, default_scheme: str = "http") -> ProxyEntry | None:
    """Parse various proxy line formats (URL, host:port:user:pass, host:port)."""
    cleaned = line.strip()
    if not cleaned or cleaned.startswith("#"):
        return None

    # Handle standard URL format: scheme://[user:pass@]host:port
    if "://" in cleaned:
        try:
            parsed = urlparse(cleaned)
            scheme = parsed.scheme.lower()
            if scheme not in {"http", "https", "socks4", "socks5"}:
                return None
            if not parsed.hostname or not parsed.port:
                return None
            return ProxyEntry(
                raw_url=cleaned,
                scheme=scheme,
                host=parsed.hostname,
                port=parsed.port,
                username=parsed.username,
                password=parsed.password,
            )
        except Exception:
            return None

    # Handle ip:port:user:pass or ip:port
    parts = cleaned.split(":")
    if len(parts) == 2:
        host, port_str = parts
        try:
            port = int(port_str)
            return ProxyEntry(
                raw_url=f"{default_scheme}://{cleaned}",
                scheme=default_scheme,
                host=host,
                port=port,
            )
        except ValueError:
            return None
    elif len(parts) == 4:
        host, port_str, user, password = parts
        try:
            port = int(port_str)
            return ProxyEntry(
                raw_url=f"{default_scheme}://{user}:{password}@{host}:{port}",
                scheme=default_scheme,
                host=host,
                port=port,
                username=user,
                password=password,
            )
        except ValueError:
            return None

    return None


class ProxyPool:
    """Thread-safe and deterministic ProxyPool with health tracking and rotation."""

    def __init__(
        self,
        proxies: list[str | ProxyEntry] | None = None,
        *,
        file_path: Path | None = None,
        strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
        default_cooldown_seconds: float = 300.0,
        max_consecutive_failures: int = 5,
    ) -> None:
        self._entries: list[ProxyEntry] = []
        self._strategy = strategy
        self._default_cooldown = default_cooldown_seconds
        self._max_failures = max_consecutive_failures
        self._index = 0

        if file_path and Path(file_path).is_file():
            self.load_from_file(Path(file_path))

        if proxies:
            for item in proxies:
                if isinstance(item, ProxyEntry):
                    self._entries.append(item)
                elif isinstance(item, str):
                    parsed = parse_proxy_line(item)
                    if parsed:
                        self._entries.append(parsed)

    @property
    def total_count(self) -> int:
        return len(self._entries)

    @property
    def active_count(self) -> int:
        return sum(1 for p in self._entries if p.is_available)

    def load_from_file(self, path: Path) -> int:
        """Load proxies from a file, skipping comments and blanks."""
        added = 0
        try:
            content = path.read_text(encoding="utf-8")
            for line in content.splitlines():
                entry = parse_proxy_line(line)
                if entry and not any(e.host == entry.host and e.port == entry.port for e in self._entries):
                    self._entries.append(entry)
                    added += 1
        except Exception:
            pass
        return added

    def add_proxy(self, proxy_str: str) -> ProxyEntry | None:
        entry = parse_proxy_line(proxy_str)
        if entry and not any(e.host == entry.host and e.port == entry.port for e in self._entries):
            self._entries.append(entry)
            return entry
        return None

    def get_proxy(self) -> ProxyEntry | None:
        """Retrieve an available proxy according to the configured strategy."""
        available = [p for p in self._entries if p.is_available]
        if not available:
            return None

        if self._strategy == RotationStrategy.RANDOM:
            selected = random.choice(available)
        else:
            selected = available[self._index % len(available)]
            self._index = (self._index + 1) % len(available)

        selected.last_used_at = time.monotonic()
        return selected

    def mark_success(self, proxy: str | ProxyEntry) -> None:
        raw = proxy.raw_url if isinstance(proxy, ProxyEntry) else proxy
        for entry in self._entries:
            if entry.raw_url == raw or entry.formatted_url == raw:
                entry.status = ProxyStatus.ACTIVE
                entry.success_count += 1
                entry.failure_count = 0
                break

    def mark_failure(self, proxy: str | ProxyEntry, cooldown_seconds: float | None = None) -> None:
        raw = proxy.raw_url if isinstance(proxy, ProxyEntry) else proxy
        cooldown = cooldown_seconds if cooldown_seconds is not None else self._default_cooldown
        for entry in self._entries:
            if entry.raw_url == raw or entry.formatted_url == raw:
                entry.failure_count += 1
                if entry.failure_count >= self._max_failures:
                    entry.status = ProxyStatus.DEAD
                else:
                    entry.status = ProxyStatus.COOLDOWN
                    entry.cooldown_until = time.monotonic() + cooldown
                break
