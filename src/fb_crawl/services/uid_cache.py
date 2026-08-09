from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from fb_crawl.core.models import UidResolution, UserRecord
from fb_crawl.exporters.atomic import atomic_text_writer


CACHE_SCHEMA_VERSION = 1


class UidResolverPort(Protocol):
    def resolve(self, browser, record: UserRecord) -> UidResolution | str: ...


class JsonProfileUidCache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._loaded = False
        self._entries: dict[str, str] = {}

    def _load(self) -> None:
        if self._loaded:
            return

        self._loaded = True

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return

        if not isinstance(payload, dict):
            return

        if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
            return

        entries = payload.get("entries")

        if not isinstance(entries, dict):
            return

        self._entries = {
            str(username).casefold(): str(uid)
            for username, uid in entries.items()
            if isinstance(username, str)
            and isinstance(uid, str)
            and uid.isdigit()
        }

    def get(self, username: str) -> str | None:
        self._load()
        return self._entries.get(username.casefold())

    def put(self, username: str, user_id: str) -> None:
        self._load()

        if not username.strip() or not user_id.isdigit():
            return

        self._entries[username.casefold()] = user_id

        with atomic_text_writer(self.path, encoding="utf-8") as file:
            json.dump(
                {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "entries": dict(sorted(self._entries.items())),
                },
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")


class CachedProfileUidResolver:
    def __init__(
        self,
        resolver: UidResolverPort,
        cache: JsonProfileUidCache,
    ) -> None:
        self._resolver = resolver
        self._cache = cache

    def resolve(
        self,
        browser,
        record: UserRecord,
        *,
        force: bool = False,
    ) -> UidResolution:
        if record.user_id.isdigit():
            return UidResolution(record.user_id, cached=True)

        username = (record.username or record.user_id).strip()
        cached = None if force else self._cache.get(username)

        if cached is not None:
            return UidResolution(cached, cached=True)

        outcome = self._resolver.resolve(browser, record)
        resolved = (
            outcome
            if isinstance(outcome, UidResolution)
            else UidResolution(str(outcome))
        )
        self._cache.put(username, resolved.user_id)
        return UidResolution(resolved.user_id, cached=False)
