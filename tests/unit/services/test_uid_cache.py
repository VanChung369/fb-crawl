import json
from pathlib import Path

from fb_crawl.core.models import UidResolution, UserRecord
from fb_crawl.services.uid_cache import (
    CachedProfileUidResolver,
    JsonProfileUidCache,
)


def _record() -> UserRecord:
    return UserRecord(
        user_id="Synthetic.User",
        username="Synthetic.User",
        name=None,
        profile_url="https://www.facebook.com/Synthetic.User",
        source="friends",
        source_url="https://www.facebook.com/example/friends",
    )


class Resolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, browser, record: UserRecord) -> UidResolution:
        self.calls += 1
        return UidResolution("100015374200952")


def test_cache_persists_each_resolution_and_reuses_it(tmp_path: Path) -> None:
    path = tmp_path / "profile-uids.json"
    resolver = Resolver()
    cached = CachedProfileUidResolver(
        resolver,
        JsonProfileUidCache(path),
    )

    first = cached.resolve(object(), _record())
    second = CachedProfileUidResolver(
        resolver,
        JsonProfileUidCache(path),
    ).resolve(object(), _record())

    assert first == UidResolution("100015374200952", cached=False)
    assert second == UidResolution("100015374200952", cached=True)
    assert resolver.calls == 1
    assert json.loads(path.read_text(encoding="utf-8"))["entries"] == {
        "synthetic.user": "100015374200952"
    }
    assert not path.with_name("profile-uids.json.tmp").exists()


def test_malformed_or_invalid_cache_entries_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "profile-uids.json"
    path.write_text(
        '{"schema_version": 1, "entries": {"synthetic.user": "bad"}}',
        encoding="utf-8",
    )
    resolver = Resolver()

    outcome = CachedProfileUidResolver(
        resolver,
        JsonProfileUidCache(path),
    ).resolve(object(), _record())

    assert outcome.cached is False
    assert resolver.calls == 1


def test_force_bypasses_existing_cache_and_replaces_mapping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profile-uids.json"
    cache = JsonProfileUidCache(path)
    cache.put("synthetic.user", "100000000000001")
    resolver = Resolver()

    outcome = CachedProfileUidResolver(
        resolver,
        JsonProfileUidCache(path),
    ).resolve(object(), _record(), force=True)

    assert outcome == UidResolution("100015374200952", cached=False)
    assert resolver.calls == 1
    assert JsonProfileUidCache(path).get("synthetic.user") == (
        "100015374200952"
    )
