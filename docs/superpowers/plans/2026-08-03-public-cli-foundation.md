# Public CLI Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an installable, offline-tested `fb-crawl public` CLI that migrates the public HTTP discovery, page/profile parsing, contact enrichment, bounded crawl, and atomic CSV/JSON export behavior from `../craw` into the new modular package.

**Architecture:** The CLI is a thin input adapter. `PublicService` coordinates injected HTTP, discovery, parser, and enrichment adapters and returns typed `ScrapeResult[PageRecord]` values. Core modules remain dependency-free; public adapters depend on `curl-cffi` and `selectolax`; exporters own serialization and atomic file replacement.

**Tech Stack:** Python 3.12+, standard-library `argparse` and `dataclasses`, `curl-cffi`, `selectolax`, `pytest`, setuptools `src` layout.

## Global Constraints

- Use Python 3.12 or newer.
- Use a `src/` package layout, standard-library `argparse`, and `dataclasses`.
- Base runtime dependencies are `curl-cffi` and `selectolax`; browser and XLSX dependencies are not part of this plan.
- `public` commands must not import Selenium, read a browser session, or create a browser.
- Automated tests must not call Facebook, search engines, or any live network endpoint.
- Keep `D:/project/fb/craw` and `D:/project/fb/Facebook-Data-Scraping-Tools` read-only.
- Do not copy session files, outputs, notebooks, caches, browser logs, or generated data from either source project.
- Keep all generated output and runtime state under Git-ignored `runtime/` by default.
- Use TDD for every behavior: observe a focused failing test before adding production behavior.
- Make one intentional commit at the end of every task and keep unrelated files out of each commit.
- Do not add automatic fallback from public mode to authenticated mode.

---

## Locked File Map

### Project and package metadata

- `pyproject.toml`: build metadata, base dependencies, `dev` extra, pytest configuration, and later the `fb-crawl` console entry point.
- `.gitignore`: virtual environments, Python caches, build products, local editor files, and the complete `runtime/` tree.
- `.env.example`: non-secret examples for supported `FB_CRAWL_` environment variables.
- `src/fb_crawl/__init__.py`: package version only.
- `src/fb_crawl/config.py`: typed settings and CLI/environment/default precedence.

### Core

- `src/fb_crawl/core/exceptions.py`: stable safe exceptions and exit-code metadata.
- `src/fb_crawl/core/models.py`: enums and immutable domain records.
- `src/fb_crawl/core/urls.py`: dependency-free Facebook URL normalization and classification.

### Public adapters

- `src/fb_crawl/adapters/http/client.py`: injectable HTTP protocol and retry-bounded `curl-cffi` client.
- `src/fb_crawl/adapters/http/discovery.py`: HTML/RSS URL extraction and keyword discovery fallback order.
- `src/fb_crawl/adapters/http/page_parser.py`: application-JSON and meta-tag parsing into `PageRecord`.
- `src/fb_crawl/adapters/http/contact_parser.py`: UID/phone extraction and optional mbasic/website enrichment.

### Application and output

- `src/fb_crawl/services/public.py`: direct, search, and breadth-first crawl orchestration.
- `src/fb_crawl/exporters/atomic.py`: same-directory temporary write followed by `os.replace`.
- `src/fb_crawl/exporters/csv.py`: stable flat public-result schema.
- `src/fb_crawl/exporters/json.py`: full result envelope serialization.
- `src/fb_crawl/cli/public.py`: public subcommands, request construction, composition, export dispatch, and summary output.
- `src/fb_crawl/cli/app.py`: root parser, safe exception mapping, and process exit code.

### Tests

- `tests/unit/`: one focused module per production unit.
- `tests/integration/test_public_service.py`: service behavior with fakes.
- `tests/integration/test_public_cli.py`: CLI-to-export behavior without network.
- `tests/fixtures/public_page.html`: minimal representative Facebook application-JSON fixture.
- `tests/fixtures/discovery/`: deterministic DuckDuckGo HTML, Bing RSS, and source HTML.

---

### Task 1: Bootstrap the installable package

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `src/fb_crawl/__init__.py`
- Create: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: no earlier task.
- Produces: importable `fb_crawl` package with `fb_crawl.__version__ == "0.1.0"`; pytest discovery under `tests/`.

- [ ] **Step 1: Write the failing package test**

```python
# tests/unit/test_package.py
import fb_crawl


def test_package_exposes_version() -> None:
    assert fb_crawl.__version__ == "0.1.0"
```

- [ ] **Step 2: Run the test and verify the package is missing**

Run: `python -m pytest tests/unit/test_package.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'fb_crawl'`.

- [ ] **Step 3: Add packaging metadata and the package version**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "fb-crawl"
version = "0.1.0"
description = "Public and authenticated Facebook data collection CLI"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  "curl-cffi",
  "selectolax",
]

[project.optional-dependencies]
dev = ["pytest"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

```python
# src/fb_crawl/__init__.py
__version__ = "0.1.0"

__all__ = ["__version__"]
```

```gitignore
# .gitignore
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.coverage
htmlcov/
build/
dist/
*.egg-info/
.idea/
.vscode/
runtime/
```

```dotenv
# .env.example
FB_CRAWL_TIMEOUT_SECONDS=20
FB_CRAWL_MAX_RETRIES=2
FB_CRAWL_OUTPUT_DIR=runtime/output
```

Create an initial README because `pyproject.toml` references it:

```markdown
# fb-crawl

Unified Facebook data collection tools. The first implementation phase provides the public CLI; authenticated browser workflows are specified separately.
```

- [ ] **Step 4: Install the package in editable development mode**

Run: `python -m pip install -e ".[dev]"`

Expected: installation succeeds and reports `fb-crawl-0.1.0` as editable.

- [ ] **Step 5: Run the focused test**

Run: `python -m pytest tests/unit/test_package.py -v`

Expected: `1 passed`.

- [ ] **Step 6: Commit the bootstrap**

```powershell
git add pyproject.toml .gitignore .env.example README.md src/fb_crawl/__init__.py tests/unit/test_package.py
git commit -m "build: bootstrap fb-crawl package"
```

---

### Task 2: Define safe exceptions and domain models

**Files:**
- Create: `src/fb_crawl/core/__init__.py`
- Create: `src/fb_crawl/core/exceptions.py`
- Create: `src/fb_crawl/core/models.py`
- Create: `tests/unit/core/test_exceptions.py`
- Create: `tests/unit/core/test_models.py`

**Interfaces:**
- Consumes: package from Task 1.
- Produces: `FbCrawlError`, `ConfigurationError`, `ValidationError`, `FetchError`, `ParseError`, `ExportError`; enums `ScrapeMode`, `PublicAction`, `TargetKind`, `ContactKind`; records `ScrapeRequest`, `ContactRecord`, `PageRecord`, `ScrapeIssue`, `ScrapeStats`, and generic `ScrapeResult[T]`.

- [ ] **Step 1: Write failing exception and model tests**

```python
# tests/unit/core/test_exceptions.py
from fb_crawl.core.exceptions import ExportError, FetchError, ValidationError


def test_errors_expose_stable_code_safe_message_and_exit_code() -> None:
    fetch = FetchError("Public fetch failed.", target="https://example.test/page")
    assert fetch.code == "public_fetch_failed"
    assert fetch.safe_message == "Public fetch failed."
    assert fetch.target == "https://example.test/page"
    assert fetch.exit_code == 1
    assert ValidationError("Bad input.").exit_code == 2
    assert ExportError("Cannot write.").exit_code == 4
```

```python
# tests/unit/core/test_models.py
import pytest

from fb_crawl.core.models import (
    ContactKind,
    ContactRecord,
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    TargetKind,
)


def test_scrape_request_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="limit must be greater than 0"):
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.PAGE,
            targets=("https://www.facebook.com/example",),
            limit=0,
        )


def test_result_reports_partial_failure_without_mutation() -> None:
    record = PageRecord(
        canonical_url="https://www.facebook.com/example",
        page_name="Example",
        contacts=(
            ContactRecord(
                kind=ContactKind.PHONE,
                value="+84 912 345 678",
                sources=("facebook:page_phone",),
            ),
        ),
    )
    issue = ScrapeIssue(
        code="public_fetch_failed",
        message="Public fetch failed.",
        target="https://www.facebook.com/bad",
        mode=ScrapeMode.PUBLIC,
        action=PublicAction.PAGE.value,
        retryable=True,
    )
    result = ScrapeResult(
        records=(record,),
        issues=(issue,),
        stats=ScrapeStats(requested=2, discovered=0, succeeded=1, failed=1),
    )
    assert result.has_failures is True
    assert result.records[0].contacts[0].value == "+84 912 345 678"
```

- [ ] **Step 2: Run the tests and verify missing modules**

Run: `python -m pytest tests/unit/core/test_exceptions.py tests/unit/core/test_models.py -v`

Expected: collection fails because `fb_crawl.core` does not exist.

- [ ] **Step 3: Implement stable exceptions**

```python
# src/fb_crawl/core/exceptions.py
class FbCrawlError(RuntimeError):
    code = "fb_crawl_error"
    exit_code = 1

    def __init__(self, safe_message: str, *, target: str | None = None) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.target = target


class ConfigurationError(FbCrawlError):
    code = "configuration_error"
    exit_code = 2


class ValidationError(FbCrawlError):
    code = "validation_error"
    exit_code = 2


class FetchError(FbCrawlError):
    code = "public_fetch_failed"
    exit_code = 1


class ParseError(FbCrawlError):
    code = "public_parse_failed"
    exit_code = 1


class ExportError(FbCrawlError):
    code = "export_failed"
    exit_code = 4
```

- [ ] **Step 4: Implement immutable domain records**

```python
# src/fb_crawl/core/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, Mapping, TypeVar


JsonScalar = str | int | float | bool | None
RecordT = TypeVar("RecordT")


class ScrapeMode(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"


class PublicAction(StrEnum):
    PAGE = "page"
    SEARCH = "search"
    CRAWL = "crawl"


class TargetKind(StrEnum):
    PAGES = "pages"
    PEOPLE = "people"
    ALL = "all"


class ContactKind(StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    WEBSITE = "website"


@dataclass(frozen=True, slots=True)
class ScrapeRequest:
    mode: ScrapeMode
    action: PublicAction | str
    targets: tuple[str, ...] = ()
    keyword: str | None = None
    target_kind: TargetKind = TargetKind.PAGES
    limit: int = 20
    depth: int = 0
    max_nodes: int = 20
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be greater than 0")
        if self.depth < 0:
            raise ValueError("depth must be 0 or greater")
        if self.max_nodes <= 0:
            raise ValueError("max_nodes must be greater than 0")
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be 0 or greater")


@dataclass(frozen=True, slots=True)
class ContactRecord:
    kind: ContactKind
    value: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageRecord:
    canonical_url: str
    page_name: str | None = None
    uid: str | None = None
    category: str | None = None
    website: str | None = None
    contacts: tuple[ContactRecord, ...] = ()
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
    depth: int = 0
    discovery_source: str = "seed"


@dataclass(frozen=True, slots=True)
class ScrapeIssue:
    code: str
    message: str
    target: str | None
    mode: ScrapeMode
    action: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class ScrapeStats:
    requested: int
    discovered: int
    succeeded: int
    failed: int


@dataclass(frozen=True, slots=True)
class ScrapeResult(Generic[RecordT]):
    records: tuple[RecordT, ...]
    issues: tuple[ScrapeIssue, ...]
    stats: ScrapeStats

    @property
    def has_failures(self) -> bool:
        return self.stats.failed > 0 or bool(self.issues)
```

Export the public names from `src/fb_crawl/core/__init__.py`; do not import adapters from that module.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/unit/core/test_exceptions.py tests/unit/core/test_models.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit domain contracts**

```powershell
git add src/fb_crawl/core tests/unit/core
git commit -m "feat: define public domain contracts"
```

---

### Task 3: Add typed configuration precedence

**Files:**
- Create: `src/fb_crawl/config.py`
- Create: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `ConfigurationError` from Task 2.
- Produces: immutable `Settings`; `load_settings(env=None, *, timeout_seconds=None, max_retries=None, output_dir=None) -> Settings`.

- [ ] **Step 1: Write failing precedence and validation tests**

```python
# tests/unit/test_config.py
from pathlib import Path

import pytest

from fb_crawl.config import load_settings
from fb_crawl.core.exceptions import ConfigurationError


def test_cli_override_wins_over_environment_and_default() -> None:
    settings = load_settings(
        {
            "FB_CRAWL_TIMEOUT_SECONDS": "30",
            "FB_CRAWL_MAX_RETRIES": "4",
            "FB_CRAWL_OUTPUT_DIR": "env-output",
        },
        timeout_seconds=5,
        output_dir=Path("cli-output"),
    )
    assert settings.timeout_seconds == 5
    assert settings.max_retries == 4
    assert settings.output_dir == Path("cli-output")


def test_invalid_environment_value_is_a_safe_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="FB_CRAWL_MAX_RETRIES"):
        load_settings({"FB_CRAWL_MAX_RETRIES": "many"})
```

- [ ] **Step 2: Run the test and verify `fb_crawl.config` is missing**

Run: `python -m pytest tests/unit/test_config.py -v`

Expected: collection fails with `ModuleNotFoundError`.

- [ ] **Step 3: Implement typed settings and precedence**

```python
# src/fb_crawl/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from fb_crawl.core.exceptions import ConfigurationError


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class Settings:
    timeout_seconds: float = 20.0
    max_retries: int = 2
    output_dir: Path = Path("runtime/output")
    user_agent: str = DEFAULT_USER_AGENT


def _number(name: str, value: str, cast: type[int] | type[float]) -> int | float:
    try:
        parsed = cast(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric.") from error
    if parsed < 0:
        raise ConfigurationError(f"{name} must be 0 or greater.")
    return parsed


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    output_dir: Path | None = None,
) -> Settings:
    values = os.environ if env is None else env
    env_timeout = values.get("FB_CRAWL_TIMEOUT_SECONDS", "20")
    env_retries = values.get("FB_CRAWL_MAX_RETRIES", "2")
    env_output = values.get("FB_CRAWL_OUTPUT_DIR", "runtime/output")
    resolved_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else float(_number("FB_CRAWL_TIMEOUT_SECONDS", env_timeout, float))
    )
    resolved_retries = (
        max_retries
        if max_retries is not None
        else int(_number("FB_CRAWL_MAX_RETRIES", env_retries, int))
    )
    if resolved_timeout <= 0:
        raise ConfigurationError("timeout_seconds must be greater than 0.")
    if resolved_retries < 0:
        raise ConfigurationError("max_retries must be 0 or greater.")
    return Settings(
        timeout_seconds=resolved_timeout,
        max_retries=resolved_retries,
        output_dir=output_dir or Path(env_output),
    )
```

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_config.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit configuration**

```powershell
git add src/fb_crawl/config.py tests/unit/test_config.py
git commit -m "feat: add typed public settings"
```

---

### Task 4: Migrate dependency-free Facebook URL rules

**Files:**
- Create: `src/fb_crawl/core/urls.py`
- Create: `tests/unit/core/test_urls.py`

**Interfaces:**
- Consumes: `TargetKind` from Task 2.
- Produces: `normalize_facebook_url`, `normalize_group_url`, `facebook_url_kind`, `url_matches_target`, and `canonicalize_targets`.

- [ ] **Step 1: Write failing URL regression tests**

```python
# tests/unit/core/test_urls.py
from fb_crawl.core.models import TargetKind
from fb_crawl.core.urls import (
    canonicalize_targets,
    facebook_url_kind,
    normalize_facebook_url,
    normalize_group_url,
)


def test_normalizes_profiles_and_rejects_internal_or_asset_paths() -> None:
    assert normalize_facebook_url(
        "https://m.facebook.com/profile.php?id=100013976614656&sk=about"
    ) == "https://www.facebook.com/profile.php?id=100013976614656"
    assert normalize_facebook_url(
        "https://www.facebook.com/people/Test/100013976614656"
    ) == "https://www.facebook.com/profile.php?id=100013976614656"
    assert normalize_facebook_url("https://www.facebook.com/login/") is None
    assert normalize_facebook_url("https://www.facebook.com/video.mpd") is None


def test_group_normalization_is_separate_from_page_targets() -> None:
    assert normalize_group_url(
        "https://m.facebook.com/groups/pythonvn?ref=share"
    ) == "https://www.facebook.com/groups/pythonvn"
    assert normalize_facebook_url("https://www.facebook.com/groups/pythonvn") is None


def test_canonicalize_targets_filters_kind_deduplicates_and_limits() -> None:
    assert canonicalize_targets(
        [
            "https://facebook.com/example?ref=one",
            "https://www.facebook.com/example#two",
            "https://www.facebook.com/profile.php?id=100013976614656",
        ],
        target=TargetKind.PAGES,
        limit=5,
    ) == ["https://www.facebook.com/example"]
    assert facebook_url_kind(
        "https://www.facebook.com/profile.php?id=100013976614656"
    ) is TargetKind.PEOPLE
```

- [ ] **Step 2: Run the tests and verify URL functions are missing**

Run: `python -m pytest tests/unit/core/test_urls.py -v`

Expected: collection fails because `fb_crawl.core.urls` is absent.

- [ ] **Step 3: Implement canonical URL rules without HTML dependencies**

Port the host, internal-path, and asset-extension sets from `../craw/scraper_helpers.py`, then implement these exact signatures:

```python
# src/fb_crawl/core/urls.py
from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from fb_crawl.core.models import TargetKind


FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "web.facebook.com",
}

FACEBOOK_INTERNAL_PATHS = {
    "about", "business", "careers", "events", "friends", "gaming", "groups",
    "help", "home.php", "legal", "lite", "login", "marketplace", "messages",
    "notifications", "pages", "people", "photo", "photo.php", "photos",
    "plugins", "policies", "privacy", "public", "qr_code_login", "recover",
    "reel", "reels", "search", "security", "settings", "share", "sharer",
    "stories", "story.php", "watch",
}

FACEBOOK_ASSET_EXTENSIONS = (
    ".avif", ".css", ".gif", ".ico", ".jpeg", ".jpg", ".js", ".json",
    ".m4s", ".map", ".mp3", ".mp4", ".mpd", ".png", ".svg", ".txt",
    ".wasm", ".webm", ".webp", ".woff", ".woff2", ".xml",
)


def _absolute_candidate(value: str, base_url: str | None) -> str:
    candidate = value.strip().replace("\\/", "/")
    if candidate.startswith("//"):
        return f"https:{candidate}"
    if candidate.startswith("/"):
        return urljoin(base_url or "https://www.facebook.com", candidate)
    return candidate


def normalize_facebook_url(
    value: str | None,
    *,
    base_url: str | None = None,
) -> str | None:
    if not value:
        return None
    parsed = urlparse(_absolute_candidate(value, base_url))
    host = parsed.netloc.lower().split(":")[0]
    if host not in FACEBOOK_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    first = parts[0]
    lowered = first.lower()
    if lowered == "profile.php":
        profile_id = parse_qs(parsed.query).get("id", [""])[0]
        return (
            f"https://www.facebook.com/profile.php?id={profile_id}"
            if profile_id.isdigit()
            else None
        )
    if lowered == "people":
        profile_id = parts[-1]
        return (
            f"https://www.facebook.com/profile.php?id={profile_id}"
            if profile_id.isdigit()
            else None
        )
    if lowered in FACEBOOK_INTERNAL_PATHS:
        return None
    if lowered.endswith(FACEBOOK_ASSET_EXTENSIONS):
        return None
    if re.fullmatch(r"[A-Za-z0-9._-]+", first) is None:
        return None
    return f"https://www.facebook.com/{first}"


def normalize_group_url(
    value: str | None,
    *,
    base_url: str | None = None,
) -> str | None:
    if not value:
        return None
    parsed = urlparse(_absolute_candidate(value, base_url))
    if parsed.netloc.lower().split(":")[0] not in FACEBOOK_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "groups":
        return None
    group_id = parts[1]
    if re.fullmatch(r"[A-Za-z0-9._-]+", group_id) is None:
        return None
    return f"https://www.facebook.com/groups/{group_id}"


def facebook_url_kind(value: str | None) -> TargetKind | None:
    normalized = normalize_facebook_url(value)
    if normalized is None:
        return None
    return TargetKind.PEOPLE if "/profile.php?id=" in normalized else TargetKind.PAGES


def url_matches_target(value: str | None, target: TargetKind) -> bool:
    kind = facebook_url_kind(value)
    return kind is not None if target is TargetKind.ALL else kind is target


def canonicalize_targets(
    values: Iterable[str],
    *,
    target: TargetKind,
    limit: int,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_facebook_url(value)
        if normalized and url_matches_target(normalized, target) and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
            if len(result) >= limit:
                break
    return result
```

- [ ] **Step 4: Run focused tests and the original URL helper tests**

Run: `python -m pytest tests/unit/core/test_urls.py -v`

Expected: all new URL tests pass.

Run from `D:/project/fb/craw`: `python -m unittest test_scraper_helpers.ScraperHelperTests.test_normalizes_facebook_profile_urls test_scraper_helpers.ScraperHelperTests.test_ignores_facebook_asset_paths_without_rejecting_dotted_usernames -v`

Expected: both source regression tests remain passing and the source tree remains unmodified.

- [ ] **Step 5: Commit URL rules**

```powershell
git add src/fb_crawl/core/urls.py tests/unit/core/test_urls.py
git commit -m "feat: normalize public Facebook targets"
```

---

### Task 5: Add the bounded injectable HTTP client

**Files:**
- Create: `src/fb_crawl/adapters/__init__.py`
- Create: `src/fb_crawl/adapters/http/__init__.py`
- Create: `src/fb_crawl/adapters/http/client.py`
- Create: `tests/unit/adapters/http/test_client.py`

**Interfaces:**
- Consumes: `Settings` and `FetchError`.
- Produces: `HttpClient` protocol with `get_text(url, *, headers=None) -> str`; `CurlHttpClient(settings, requester=requests.get, sleep_func=time.sleep)`.

- [ ] **Step 1: Write failing retry and sanitization tests**

```python
# tests/unit/adapters/http/test_client.py
from pathlib import Path

import pytest

from fb_crawl.adapters.http.client import CurlHttpClient
from fb_crawl.config import Settings
from fb_crawl.core.exceptions import FetchError


class FakeResponse:
    text = "<html>ok</html>"

    def raise_for_status(self) -> None:
        return None


def test_client_returns_text_and_uses_timeout_and_headers() -> None:
    calls: list[dict[str, object]] = []

    def requester(url: str, **kwargs: object) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    client = CurlHttpClient(
        Settings(timeout_seconds=7, max_retries=0, output_dir=Path("runtime/output")),
        requester=requester,
        sleep_func=lambda _: None,
    )
    assert client.get_text("https://example.test/page") == "<html>ok</html>"
    assert calls[0]["timeout"] == 7
    assert "User-Agent" in calls[0]["headers"]


def test_client_retries_only_configured_times_and_hides_query() -> None:
    attempts = 0

    def requester(url: str, **kwargs: object) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("transport detail")

    client = CurlHttpClient(
        Settings(timeout_seconds=1, max_retries=2),
        requester=requester,
        sleep_func=lambda _: None,
    )
    with pytest.raises(FetchError) as caught:
        client.get_text("https://example.test/page?token=private")
    assert attempts == 3
    assert "token" not in caught.value.safe_message
    assert caught.value.target == "https://example.test/page"
```

- [ ] **Step 2: Run the tests and verify the HTTP adapter is missing**

Run: `python -m pytest tests/unit/adapters/http/test_client.py -v`

Expected: collection fails because the client module does not exist.

- [ ] **Step 3: Implement the protocol and retry-bounded client**

```python
# src/fb_crawl/adapters/http/client.py
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from curl_cffi import requests

from fb_crawl.config import Settings
from fb_crawl.core.exceptions import FetchError


class HttpClient(Protocol):
    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str: ...


def _safe_target(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class CurlHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        requester: Callable[..., Any] = requests.get,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._requester = requester
        self._sleep = sleep_func

    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        request_headers = {
            "User-Agent": self._settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            **dict(headers or {}),
        }
        last_error: Exception | None = None
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = self._requester(
                    url,
                    headers=request_headers,
                    timeout=self._settings.timeout_seconds,
                )
                response.raise_for_status()
                return str(response.text)
            except Exception as error:
                last_error = error
                if attempt < self._settings.max_retries:
                    self._sleep(0.25 * (2**attempt))
        safe_target = _safe_target(url)
        raise FetchError(
            f"Public fetch failed for {safe_target}.",
            target=safe_target,
        ) from last_error
```

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/adapters/http/test_client.py -v`

Expected: all tests pass with no network access.

- [ ] **Step 5: Commit the HTTP boundary**

```powershell
git add src/fb_crawl/adapters tests/unit/adapters/http/test_client.py
git commit -m "feat: add bounded public HTTP client"
```

---

### Task 6: Migrate public URL discovery and search fallback

**Files:**
- Create: `src/fb_crawl/adapters/http/discovery.py`
- Create: `tests/unit/adapters/http/test_discovery.py`
- Create: `tests/fixtures/discovery/source.html`
- Create: `tests/fixtures/discovery/duckduckgo.html`
- Create: `tests/fixtures/discovery/bing.xml`

**Interfaces:**
- Consumes: `HttpClient`, `TargetKind`, and core URL functions.
- Produces: query builders, pure extraction functions, and `PublicDiscovery.search(keyword, target, limit) -> list[str]`; `PublicDiscovery.from_html(html, *, base_url, target, limit) -> list[str]`.

- [ ] **Step 1: Add deterministic fixture content**

```html
<!-- tests/fixtures/discovery/source.html -->
<a href="/alpha.page?ref=source">Alpha</a>
<a href="https://www.facebook.com/profile.php?id=100013976614656">Person</a>
<a href="https://www.facebook.com/login/">Login</a>
<a href="https://www.facebook.com/groups/pythonvn">Group</a>
```

```html
<!-- tests/fixtures/discovery/duckduckgo.html -->
<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2Flamspahcm%2F">Lam Spa</a>
```

```xml
<!-- tests/fixtures/discovery/bing.xml -->
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item><title>Fallback Spa</title><link>https://www.facebook.com/fallbackspa/</link></item>
</channel></rss>
```

- [ ] **Step 2: Write failing extraction and fallback tests**

```python
# tests/unit/adapters/http/test_discovery.py
from pathlib import Path

from fb_crawl.adapters.http.discovery import PublicDiscovery, extract_facebook_urls
from fb_crawl.core.models import TargetKind


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "discovery"


class MappingClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get_text(self, url: str, *, headers=None) -> str:
        self.calls.append(url)
        return self.pages[url]


def test_extracts_and_filters_canonical_urls_from_source_html() -> None:
    html = (FIXTURES / "source.html").read_text(encoding="utf-8")
    assert extract_facebook_urls(
        html,
        base_url="https://www.facebook.com/search/pages?q=test",
        target=TargetKind.ALL,
        limit=10,
    ) == [
        "https://www.facebook.com/alpha.page",
        "https://www.facebook.com/profile.php?id=100013976614656",
    ]


def test_search_falls_back_to_bing_when_duckduckgo_is_empty() -> None:
    client = MappingClient({})
    discovery = PublicDiscovery(client)
    ddg_url, bing_url = discovery.query_urls("spa", TargetKind.PAGES)[0]
    client.pages[ddg_url] = "<html></html>"
    client.pages[bing_url] = (FIXTURES / "bing.xml").read_text(encoding="utf-8")
    assert discovery.search("spa", TargetKind.PAGES, 5) == [
        "https://www.facebook.com/fallbackspa"
    ]
    assert client.calls == [ddg_url, bing_url]
```

- [ ] **Step 3: Run the tests and verify discovery is missing**

Run: `python -m pytest tests/unit/adapters/http/test_discovery.py -v`

Expected: collection fails because `discovery.py` is absent.

- [ ] **Step 4: Implement pure discovery functions**

Implement the following functions by adapting the tested behavior from `../craw/scraper_helpers.py`:

```python
# src/fb_crawl/adapters/http/discovery.py
from __future__ import annotations

import html as html_module
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, unquote, urlparse

from selectolax.parser import HTMLParser

from fb_crawl.adapters.http.client import HttpClient
from fb_crawl.core.models import TargetKind
from fb_crawl.core.urls import canonicalize_targets, normalize_facebook_url, url_matches_target


FACEBOOK_URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.|mbasic\.|web\.)?facebook\.com/[^\s\"'<>\\]+",
    re.IGNORECASE,
)


def _decode(value: str) -> str:
    decoded = html_module.unescape(value)
    try:
        return html_module.unescape(json.loads(f'"{decoded}"'))
    except json.JSONDecodeError:
        return decoded.replace("\\/", "/")


def duckduckgo_query_url(query: str) -> str:
    return f"https://html.duckduckgo.com/html/?q={quote(query)}"


def bing_rss_query_url(query: str) -> str:
    return f"https://www.bing.com/search?format=rss&q={quote(query)}"


def facebook_public_search_url(keyword: str) -> str:
    slug = quote(re.sub(r"\s+", "-", keyword.strip()).strip("-"))
    return f"https://www.facebook.com/public/{slug}"


def keyword_queries(keyword: str, target: TargetKind) -> list[str]:
    cleaned = keyword.strip()
    if target is TargetKind.PEOPLE:
        return [
            f"site:facebook.com/profile.php?id= {cleaned}",
            f"site:facebook.com/people {cleaned}",
        ]
    if target is TargetKind.PAGES:
        return [
            f"site:facebook.com {cleaned} -site:facebook.com/profile.php "
            "-site:facebook.com/people -site:facebook.com/groups"
        ]
    return [f"site:facebook.com {cleaned}"]


def extract_facebook_urls(
    text: str | None,
    *,
    base_url: str | None,
    target: TargetKind,
    limit: int,
) -> list[str]:
    if not text or limit <= 0:
        return []
    decoded = _decode(text)
    candidates = [
        node.attributes["href"]
        for node in HTMLParser(decoded).css("a[href]")
        if node.attributes.get("href")
    ]
    candidates.extend(match.group(0) for match in FACEBOOK_URL_PATTERN.finditer(decoded))
    normalized = [
        value
        for candidate in candidates
        if (
            value := normalize_facebook_url(
                candidate.rstrip(".,);]}'\""),
                base_url=base_url,
            )
        )
    ]
    return canonicalize_targets(normalized, target=target, limit=limit)


def extract_duckduckgo_urls(text: str, target: TargetKind, limit: int) -> list[str]:
    candidates: list[str] = []
    for node in HTMLParser(text).css("a[href]"):
        href = node.attributes.get("href", "")
        parsed = urlparse(f"https:{href}" if href.startswith("//") else href)
        redirect = parse_qs(parsed.query).get("uddg", [""])[0]
        candidate = unquote(redirect) if redirect else href
        if normalize_facebook_url(candidate):
            candidates.append(candidate)
    return canonicalize_targets(candidates, target=target, limit=limit)


def extract_bing_urls(text: str, target: TargetKind, limit: int) -> list[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    return canonicalize_targets(
        [item.findtext("link") or "" for item in root.findall(".//item")],
        target=target,
        limit=limit,
    )
```

- [ ] **Step 5: Implement the discovery adapter with deterministic fallback**

```python
class PublicDiscovery:
    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def query_urls(
        self,
        keyword: str,
        target: TargetKind,
    ) -> list[tuple[str, str]]:
        return [
            (duckduckgo_query_url(query), bing_rss_query_url(query))
            for query in keyword_queries(keyword, target)
        ]

    def from_html(
        self,
        html: str,
        *,
        base_url: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]:
        return extract_facebook_urls(
            html,
            base_url=base_url,
            target=target,
            limit=limit,
        )

    def search(
        self,
        keyword: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]:
        found: list[str] = []
        for ddg_url, bing_url in self.query_urls(keyword, target):
            remaining = limit - len(found)
            if remaining <= 0:
                break
            ddg_html = self._client.get_text(ddg_url)
            batch = extract_duckduckgo_urls(ddg_html, target, remaining)
            if not batch:
                bing_xml = self._client.get_text(bing_url)
                batch = extract_bing_urls(bing_xml, target, remaining)
            found = canonicalize_targets(
                [*found, *batch],
                target=target,
                limit=limit,
            )
        if target in {TargetKind.PEOPLE, TargetKind.ALL} and len(found) < limit:
            directory_url = facebook_public_search_url(keyword)
            directory_html = self._client.get_text(directory_url)
            found = canonicalize_targets(
                [
                    *found,
                    *self.from_html(
                        directory_html,
                        base_url=directory_url,
                        target=target,
                        limit=limit - len(found),
                    ),
                ],
                target=target,
                limit=limit,
            )
        return found
```

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/unit/adapters/http/test_discovery.py -v`

Expected: all tests pass without network.

- [ ] **Step 7: Commit discovery**

```powershell
git add src/fb_crawl/adapters/http/discovery.py tests/unit/adapters/http/test_discovery.py tests/fixtures/discovery
git commit -m "feat: discover public Facebook targets"
```

---

### Task 7: Parse public page/profile information

**Files:**
- Create: `src/fb_crawl/adapters/http/page_parser.py`
- Create: `tests/unit/adapters/http/test_page_parser.py`
- Create: `tests/fixtures/public_page.html`

**Interfaces:**
- Consumes: `PageRecord`, `ContactRecord`, `ContactKind`, and `ParseError`.
- Produces: `PublicPageParser.parse(html: str, canonical_url: str) -> PageRecord`.

- [ ] **Step 1: Create a compact representative HTML fixture**

```html
<!-- tests/fixtures/public_page.html -->
<html><head>
  <meta name="description" content="1,234 likes · 56 talking about this · 78 were here">
</head><body>
<script type="application/json">
{"payload":{"profile_header_renderer":{"user":{
  "name":"Example Spa",
  "url":"https://www.facebook.com/examplespa",
  "delegate_page":{"id":"1156899667774877","is_business_page_active":true},
  "profilePicLarge":{"uri":"https://cdn.example/profile.jpg"},
  "cover_photo":{"photo":{"image":{"uri":"https://cdn.example/cover.jpg"}}}
}}}}
</script>
<script type="application/json">
{"payload":{"profile_tile_items":{"nodes":[
  {"node":{"timeline_context_item":{
    "timeline_context_list_item_type":"INTRO_CARD_INFLUENCER_CATEGORY",
    "renderer":{"context_item":{"title":{"text":"Spa"}}}
  }}},
  {"node":{"timeline_context_item":{
    "timeline_context_list_item_type":"INTRO_CARD_PROFILE_PHONE",
    "renderer":{"context_item":{"title":{"text":"+84 912 345 678"}}}
  }}},
  {"node":{"timeline_context_item":{
    "timeline_context_list_item_type":"INTRO_CARD_WEBSITE",
    "renderer":{"context_item":{"title":{"text":"example.com"}}}
  }}}
]}}}
</script>
</body></html>
```

- [ ] **Step 2: Write failing parser tests**

```python
# tests/unit/adapters/http/test_page_parser.py
from pathlib import Path

import pytest

from fb_crawl.adapters.http.page_parser import PublicPageParser
from fb_crawl.core.exceptions import ParseError
from fb_crawl.core.models import ContactKind


FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "public_page.html"


def test_parser_builds_typed_page_record() -> None:
    record = PublicPageParser().parse(
        FIXTURE.read_text(encoding="utf-8"),
        "https://www.facebook.com/examplespa",
    )
    assert record.page_name == "Example Spa"
    assert record.uid == "1156899667774877"
    assert record.category == "Spa"
    assert record.website == "example.com"
    assert record.metadata["likes_count"] == "1,234"
    assert [(contact.kind, contact.value) for contact in record.contacts] == [
        (ContactKind.PHONE, "+84 912 345 678"),
        (ContactKind.WEBSITE, "example.com"),
    ]


def test_parser_raises_safe_error_when_no_page_data_exists() -> None:
    with pytest.raises(ParseError, match="No public page data found"):
        PublicPageParser().parse("<html></html>", "https://www.facebook.com/empty")
```

- [ ] **Step 3: Run the tests and verify the parser is missing**

Run: `python -m pytest tests/unit/adapters/http/test_page_parser.py -v`

Expected: collection fails because `page_parser.py` is absent.

- [ ] **Step 4: Implement JSON traversal and profile-card parsing**

```python
# src/fb_crawl/adapters/http/page_parser.py
from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from typing import Any

from selectolax.parser import HTMLParser

from fb_crawl.core.exceptions import ParseError
from fb_crawl.core.models import ContactKind, ContactRecord, PageRecord


PROFILE_TYPES = {
    "INTRO_CARD_INFLUENCER_CATEGORY": "category",
    "INTRO_CARD_PROFILE_PHONE": ContactKind.PHONE,
    "INTRO_CARD_PROFILE_EMAIL": ContactKind.EMAIL,
    "INTRO_CARD_WEBSITE": ContactKind.WEBSITE,
}


def _walk_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _documents(parser: HTMLParser) -> list[Mapping[str, Any]]:
    documents: list[Mapping[str, Any]] = []
    for script in parser.css('script[type="application/json"]'):
        try:
            value = json.loads(script.text(strip=True))
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            documents.append(value)
    return documents


def _title_text(context: Mapping[str, Any]) -> str | None:
    renderer = context.get("renderer") or {}
    item = renderer.get("context_item") or {}
    title = item.get("title") or {}
    value = title.get("text")
    return str(value).strip() if value else None


class PublicPageParser:
    def parse(self, html: str, canonical_url: str) -> PageRecord:
        parser = HTMLParser(html)
        mappings = [
            mapping
            for document in _documents(parser)
            for mapping in _walk_mappings(document)
        ]
        user: Mapping[str, Any] | None = None
        for mapping in mappings:
            renderer = mapping.get("profile_header_renderer")
            if isinstance(renderer, Mapping) and isinstance(renderer.get("user"), Mapping):
                user = renderer["user"]
                break

        category: str | None = None
        contacts: list[ContactRecord] = []
        website: str | None = None
        for mapping in mappings:
            item_type = mapping.get("timeline_context_list_item_type")
            mapped = PROFILE_TYPES.get(str(item_type))
            value = _title_text(mapping)
            if not mapped or not value:
                continue
            if mapped == "category":
                category = value
                continue
            contacts.append(
                ContactRecord(
                    kind=mapped,
                    value=value,
                    sources=("facebook:profile_card",),
                )
            )
            if mapped is ContactKind.WEBSITE:
                website = value

        metadata: dict[str, str | bool | None] = {}
        description_node = parser.css_first('meta[name="description"]')
        description = (
            description_node.attributes.get("content", "")
            if description_node is not None
            else ""
        )
        for key, pattern in {
            "likes_count": r"([\d,]+)\s+likes",
            "talking_count": r"([\d,]+)\s+talking about this",
            "were_here_count": r"([\d,]+)\s+were here",
        }.items():
            match = re.search(pattern, description, re.IGNORECASE)
            metadata[key] = match.group(1) if match else None

        delegate = user.get("delegate_page") if user else None
        uid = delegate.get("id") if isinstance(delegate, Mapping) else None
        if isinstance(delegate, Mapping):
            metadata["is_business_page"] = delegate.get("is_business_page_active")
        if user:
            profile_pic = user.get("profilePicLarge") or user.get("profilePicMedium") or {}
            cover = user.get("cover_photo") or {}
            photo = cover.get("photo") or {}
            image = photo.get("image") or {}
            metadata["profile_pic"] = profile_pic.get("uri")
            metadata["cover_photo"] = image.get("uri")

        if user is None and not contacts and all(value is None for value in metadata.values()):
            raise ParseError("No public page data found.", target=canonical_url)

        return PageRecord(
            canonical_url=canonical_url,
            page_name=str(user.get("name")) if user and user.get("name") else None,
            uid=str(uid) if uid else None,
            category=category,
            website=website,
            contacts=tuple(contacts),
            metadata=metadata,
        )
```

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/unit/adapters/http/test_page_parser.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit the page parser**

```powershell
git add src/fb_crawl/adapters/http/page_parser.py tests/unit/adapters/http/test_page_parser.py tests/fixtures/public_page.html
git commit -m "feat: parse public Facebook pages"
```

---

### Task 8: Extract and enrich contact details

**Files:**
- Create: `src/fb_crawl/adapters/http/contact_parser.py`
- Create: `tests/unit/adapters/http/test_contact_parser.py`

**Interfaces:**
- Consumes: `HttpClient`, `PageRecord`, `ContactRecord`, `ScrapeIssue`, and `FetchError`.
- Produces: pure phone/UID helpers; `ContactEnricher.enrich(record, facebook_html) -> tuple[PageRecord, tuple[ScrapeIssue, ...]]`.

- [ ] **Step 1: Write failing extraction and enrichment tests**

```python
# tests/unit/adapters/http/test_contact_parser.py
from fb_crawl.adapters.http.contact_parser import (
    ContactEnricher,
    extract_phone_numbers,
    extract_raw_phone_numbers,
    extract_uid,
)
from fb_crawl.core.models import ContactKind, PageRecord


class MappingClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def get_text(self, url: str, *, headers=None) -> str:
        return self.pages[url]


def test_phone_and_uid_helpers_preserve_regression_behavior() -> None:
    assert extract_phone_numbers(
        "Hotline: 0123 456 789; WhatsApp: +84 987-654-321",
        require_context=True,
    ) == ["0123 456 789", "+84 987-654-321"]
    assert extract_raw_phone_numbers(
        r'{"formatted_phone_number":"\u002b84 912 345 678"}'
    ) == ["+84 912 345 678"]
    assert extract_uid('{"pageID":"1156899667774877"}') == "1156899667774877"


def test_enricher_merges_sources_without_duplicate_phone_values() -> None:
    record = PageRecord(
        canonical_url="https://www.facebook.com/examplespa",
        website="example.com",
    )
    client = MappingClient(
        {
            "https://mbasic.facebook.com/examplespa/about": "Hotline: +84 912 345 678",
            "https://example.com": '<a href="tel:+84912345678">Call</a>',
        }
    )
    enriched, issues = ContactEnricher(client).enrich(
        record,
        '{"pageID":"1156899667774877"}',
    )
    phones = [item for item in enriched.contacts if item.kind is ContactKind.PHONE]
    assert enriched.uid == "1156899667774877"
    assert len(phones) == 1
    assert set(phones[0].sources) == {
        "facebook:mbasic_about_text",
        "website:tel_or_whatsapp",
    }
    assert issues == ()
```

- [ ] **Step 2: Run tests and verify contact parser is missing**

Run: `python -m pytest tests/unit/adapters/http/test_contact_parser.py -v`

Expected: collection fails because `contact_parser.py` is absent.

- [ ] **Step 3: Implement pure extraction helpers**

Port the tested Unicode-safe constants and regex patterns from `../craw/scraper_helpers.py`. Keep UTF-8 Vietnamese context words intact. Implement these exact public helpers:

```python
# src/fb_crawl/adapters/http/contact_parser.py
from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

from selectolax.parser import HTMLParser

from fb_crawl.adapters.http.client import HttpClient
from fb_crawl.core.exceptions import FetchError
from fb_crawl.core.models import (
    ContactKind,
    ContactRecord,
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
)


PHONE_CONTEXT_WORDS = (
    "phone", "mobile", "tel", "telephone", "hotline", "call", "contact",
    "whatsapp", "zalo", "viber", "sdt", "sđt", "so dien thoai",
    "số điện thoại", "dien thoai", "điện thoại", "lien he", "liên hệ",
)

PHONE_PATTERN = re.compile(
    r"(?<![\w])(?:\+?\d{1,4}[\s().\-/]*)?(?:\(?\d{2,5}\)?[\s().\-/]*)?"
    r"\d{3,4}[\s().\-/]*\d{3,4}(?:[\s().\-/]*\d{2,4})?(?![\w])"
)

UID_PATTERNS = (
    re.compile(r'"pageID"\s*:\s*"(\d{5,})"'),
    re.compile(r'"profile_id"\s*:\s*"(\d{5,})"'),
    re.compile(r'"userID"\s*:\s*"(\d{5,})"'),
    re.compile(r"(?:owner_id|ownerID)=(\d{5,})"),
    re.compile(r"profile\.php\?id=(\d{5,})"),
)

RAW_PHONE_PATTERNS = (
    re.compile(r'"formatted_phone_number"\s*:\s*"([^"]+)"'),
    re.compile(r'"phone_number"\s*:\s*"([^"]+)"'),
    re.compile(r'"phoneNumber"\s*:\s*"([^"]+)"'),
    re.compile(r'"mobile_phone"\s*:\s*"([^"]+)"'),
)


def _decode(value: str) -> str:
    decoded = html_module.unescape(value)
    try:
        return html_module.unescape(json.loads(f'"{decoded}"'))
    except json.JSONDecodeError:
        return decoded.replace("\\/", "/")


def _phone_key(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if not 8 <= len(digits) <= 15 or len(set(digits)) == 1:
        return None
    return digits


def extract_phone_numbers(text: str | None, *, require_context: bool = False) -> list[str]:
    if not text:
        return []
    found: dict[str, str] = {}
    for chunk in re.split(r"[\n\r|•;]+", text):
        if require_context and not any(word in chunk.lower() for word in PHONE_CONTEXT_WORDS):
            continue
        for match in PHONE_PATTERN.finditer(chunk):
            value = re.sub(r"\s+", " ", match.group(0)).strip(" .,-/()")
            key = _phone_key(value)
            if key and key not in found:
                found[key] = value
    return list(found.values())


def extract_raw_phone_numbers(text: str | None) -> list[str]:
    if not text:
        return []
    values: list[str] = []
    decoded = _decode(text)
    for pattern in RAW_PHONE_PATTERNS:
        for match in pattern.finditer(decoded):
            values.extend(extract_phone_numbers(_decode(match.group(1))))
    unique: dict[str, str] = {}
    for value in values:
        if key := _phone_key(value):
            unique.setdefault(key, value)
    return list(unique.values())


def extract_uid(text: str | None) -> str | None:
    if not text:
        return None
    for pattern in UID_PATTERNS:
        if match := pattern.search(text):
            return match.group(1)
    return None


def visible_text(html: str) -> str:
    parser = HTMLParser(html)
    for selector in ("script", "style", "noscript"):
        for node in parser.css(selector):
            node.decompose()
    return parser.text(separator="\n", strip=True)


def tel_link_text(html: str) -> str:
    parser = HTMLParser(html)
    return "\n".join(
        href.removeprefix("tel:") if href.lower().startswith("tel:") else href
        for node in parser.css("a[href]")
        if (href := node.attributes.get("href", ""))
        and (href.lower().startswith("tel:") or "wa.me/" in href.lower() or "whatsapp" in href.lower())
    )
```

- [ ] **Step 4: Implement contact merging and optional enrichment**

Add the complete normalization, source-merging, and enrichment-target helpers before `ContactEnricher`:

```python
def _normalize_website(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    candidate = value.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _facebook_identifier(url: str) -> str | None:
    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("id", [""])[0]
    if query_id:
        return query_id
    parts = [part for part in parsed.path.split("/") if part]
    if not parts or parts[0].lower() == "profile.php":
        return None
    return parts[0]


def _merge_phone(
    contacts: list[ContactRecord],
    value: str,
    source: str,
) -> None:
    key = _phone_key(value)
    if key is None:
        return
    for index, existing in enumerate(contacts):
        if existing.kind is not ContactKind.PHONE:
            continue
        if _phone_key(existing.value) == key:
            contacts[index] = replace(
                existing,
                sources=tuple(dict.fromkeys((*existing.sources, source))),
            )
            return
    contacts.append(
        ContactRecord(
            kind=ContactKind.PHONE,
            value=value,
            sources=(source,),
        )
    )


def _enrichment_targets(
    record: PageRecord,
) -> list[tuple[str, str, Callable[[str], str]]]:
    targets: list[tuple[str, str, Callable[[str], str]]] = []
    identifier = _facebook_identifier(record.canonical_url)
    if identifier:
        targets.append(
            (
                f"https://mbasic.facebook.com/{identifier}/about",
                "facebook:mbasic_about_text",
                visible_text,
            )
        )
    website = _normalize_website(record.website)
    if website:
        targets.append(
            (
                website,
                "website:tel_or_whatsapp",
                tel_link_text,
            )
        )
    return targets


class ContactEnricher:
    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def enrich(
        self,
        record: PageRecord,
        facebook_html: str,
    ) -> tuple[PageRecord, tuple[ScrapeIssue, ...]]:
        uid = record.uid or extract_uid(facebook_html)
        contacts = list(record.contacts)
        issues: list[ScrapeIssue] = []

        def add_phones(text: str, source: str, require_context: bool = False) -> None:
            for phone in extract_phone_numbers(text, require_context=require_context):
                _merge_phone(contacts, phone, source)

        add_phones("\n".join(extract_raw_phone_numbers(facebook_html)), "facebook:raw_phone_field")
        add_phones(visible_text(facebook_html), "facebook:public_text", require_context=True)

        for url, source, extractor in _enrichment_targets(record):
            try:
                body = self._client.get_text(url)
            except FetchError as error:
                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=error.target,
                        mode=ScrapeMode.PUBLIC,
                        action=PublicAction.PAGE.value,
                        retryable=True,
                    )
                )
                continue
            if source == "facebook:mbasic_about_text" and uid is None:
                uid = extract_uid(body)
            add_phones(extractor(body), source, require_context="text" in source)

        return replace(record, uid=uid, contacts=tuple(contacts)), tuple(issues)
```

- [ ] **Step 5: Run contact tests and source regression tests**

Run: `python -m pytest tests/unit/adapters/http/test_contact_parser.py -v`

Expected: all tests pass.

Run from `D:/project/fb/craw`: `python -m unittest test_scraper_helpers.ScraperHelperTests.test_extracts_multiple_phone_numbers_from_labeled_text test_scraper_helpers.ScraperHelperTests.test_extracts_escaped_formatted_phone_number_from_raw_facebook_json -v`

Expected: both source regression tests remain passing.

- [ ] **Step 6: Commit contact extraction**

```powershell
git add src/fb_crawl/adapters/http/contact_parser.py tests/unit/adapters/http/test_contact_parser.py
git commit -m "feat: enrich public contact records"
```

---

### Task 9: Orchestrate direct, search, and bounded crawl use cases

**Files:**
- Create: `src/fb_crawl/services/__init__.py`
- Create: `src/fb_crawl/services/public.py`
- Create: `tests/integration/test_public_service.py`

**Interfaces:**
- Consumes: `HttpClient`, `PublicDiscovery`, `PublicPageParser`, `ContactEnricher`, core URL rules, and all public domain records.
- Produces: `PublicService.run(request: ScrapeRequest) -> ScrapeResult[PageRecord]`.

- [ ] **Step 1: Write failing service tests with fakes**

```python
# tests/integration/test_public_service.py
from fb_crawl.core.exceptions import FetchError
from fb_crawl.core.models import (
    PageRecord,
    PublicAction,
    ScrapeMode,
    ScrapeRequest,
    TargetKind,
)
from fb_crawl.services.public import PublicService


class FakeClient:
    def __init__(self, pages: dict[str, str | Exception]) -> None:
        self.pages = pages

    def get_text(self, url: str, *, headers=None) -> str:
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


class FakeDiscovery:
    def __init__(self, links: dict[str, list[str]]) -> None:
        self.links = links

    def search(self, keyword: str, target: TargetKind, limit: int) -> list[str]:
        return self.links[keyword][:limit]

    def from_html(self, html: str, *, base_url: str, target: TargetKind, limit: int) -> list[str]:
        return self.links.get(base_url, [])[:limit]


class FakeParser:
    def parse(self, html: str, canonical_url: str) -> PageRecord:
        return PageRecord(canonical_url=canonical_url, page_name=html)


class FakeEnricher:
    def enrich(self, record: PageRecord, facebook_html: str):
        return record, ()


def build_service(pages: dict[str, str | Exception], links: dict[str, list[str]]) -> PublicService:
    return PublicService(FakeClient(pages), FakeDiscovery(links), FakeParser(), FakeEnricher())


def test_direct_batch_keeps_success_when_another_target_fails() -> None:
    good = "https://www.facebook.com/good"
    bad = "https://www.facebook.com/bad"
    result = build_service(
        {good: "Good Page", bad: FetchError("Public fetch failed.", target=bad)},
        {},
    ).run(
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.PAGE,
            targets=(good, bad),
            target_kind=TargetKind.ALL,
            limit=2,
            max_nodes=2,
        )
    )
    assert [record.page_name for record in result.records] == ["Good Page"]
    assert result.stats.succeeded == 1
    assert result.stats.failed == 1
    assert result.issues[0].target == bad


def test_crawl_is_breadth_first_deduplicated_and_depth_bounded() -> None:
    seed = "https://www.facebook.com/seed"
    next_url = "https://www.facebook.com/next"
    too_deep = "https://www.facebook.com/too-deep"
    result = build_service(
        {seed: "Seed", next_url: "Next"},
        {seed: [next_url, seed], next_url: [too_deep]},
    ).run(
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.CRAWL,
            targets=(seed,),
            target_kind=TargetKind.ALL,
            depth=1,
            max_nodes=10,
            limit=10,
        )
    )
    assert [(item.canonical_url, item.depth) for item in result.records] == [
        (seed, 0),
        (next_url, 1),
    ]


def test_crawl_accepts_public_group_as_a_discovery_seed() -> None:
    group = "https://www.facebook.com/groups/pythonvn"
    member = "https://www.facebook.com/profile.php?id=100013976614656"
    result = build_service(
        {group: "Group HTML", member: "Member"},
        {group: [member]},
    ).run(
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.CRAWL,
            targets=(group,),
            target_kind=TargetKind.ALL,
            depth=0,
            max_nodes=10,
            limit=10,
        )
    )
    assert [(item.canonical_url, item.discovery_source) for item in result.records] == [
        (member, group)
    ]
```

- [ ] **Step 2: Run the tests and verify the service is missing**

Run: `python -m pytest tests/integration/test_public_service.py -v`

Expected: collection fails because `services.public` is absent.

- [ ] **Step 3: Implement public service orchestration**

```python
# src/fb_crawl/services/public.py
from __future__ import annotations

import time
from collections import deque
from dataclasses import replace
from typing import Protocol

from fb_crawl.adapters.http.client import HttpClient
from fb_crawl.core.exceptions import FetchError, ParseError, ValidationError
from fb_crawl.core.models import (
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    TargetKind,
)
from fb_crawl.core.urls import (
    canonicalize_targets,
    normalize_facebook_url,
    normalize_group_url,
)


class DiscoveryPort(Protocol):
    def search(self, keyword: str, target: TargetKind, limit: int) -> list[str]: ...
    def from_html(
        self,
        html: str,
        *,
        base_url: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]: ...


class PageParserPort(Protocol):
    def parse(self, html: str, canonical_url: str) -> PageRecord: ...


class ContactEnricherPort(Protocol):
    def enrich(
        self,
        record: PageRecord,
        facebook_html: str,
    ) -> tuple[PageRecord, tuple[ScrapeIssue, ...]]: ...


class PublicService:
    def __init__(
        self,
        client: HttpClient,
        discovery: DiscoveryPort,
        parser: PageParserPort,
        enricher: ContactEnricherPort,
        *,
        sleep_func=time.sleep,
    ) -> None:
        self._client = client
        self._discovery = discovery
        self._parser = parser
        self._enricher = enricher
        self._sleep = sleep_func

    def _initial_targets(self, request: ScrapeRequest) -> list[tuple[str, str]]:
        if request.mode is not ScrapeMode.PUBLIC:
            raise ValidationError("PublicService requires public mode.")
        action = PublicAction(request.action)
        if action is PublicAction.SEARCH:
            if not request.keyword or not request.keyword.strip():
                raise ValidationError("Search requires a non-empty keyword.")
            keyword = request.keyword.strip()
            return [
                (url, f"keyword:{keyword}")
                for url in self._discovery.search(
                    keyword,
                    request.target_kind,
                    request.limit,
                )
            ]

        seeds: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(url: str, source: str) -> None:
            if url not in seen and len(seeds) < request.limit:
                seen.add(url)
                seeds.append((url, source))

        for target in canonicalize_targets(
            request.targets,
            target=request.target_kind,
            limit=request.limit,
        ):
            add(target, "seed")

        if action is PublicAction.CRAWL:
            for raw_target in request.targets:
                group_url = normalize_group_url(raw_target)
                if not group_url or len(seeds) >= request.limit:
                    continue
                group_html = self._client.get_text(group_url)
                for target in self._discovery.from_html(
                    group_html,
                    base_url=group_url,
                    target=request.target_kind,
                    limit=request.limit - len(seeds),
                ):
                    add(target, group_url)

        if not seeds:
            raise ValidationError("No valid public Facebook targets were provided.")
        return seeds

    def run(self, request: ScrapeRequest) -> ScrapeResult[PageRecord]:
        action = PublicAction(request.action)
        seeds = self._initial_targets(request)
        targets = [url for url, _ in seeds]
        initial_discovered = sum(source != "seed" for _, source in seeds)
        queue = deque(
            (url, 0, source)
            for url, source in seeds[: request.max_nodes]
        )
        queued = set(targets[: request.max_nodes])
        visited: set[str] = set()
        records: list[PageRecord] = []
        issues: list[ScrapeIssue] = []
        failed = 0

        while queue and len(records) + failed < request.max_nodes:
            url, depth, source = queue.popleft()
            queued.discard(url)
            if url in visited:
                continue
            visited.add(url)
            try:
                html = self._client.get_text(url)
                parsed = self._parser.parse(html, url)
                enriched, enrichment_issues = self._enricher.enrich(parsed, html)
                records.append(
                    replace(enriched, depth=depth, discovery_source=source)
                )
                issues.extend(enrichment_issues)
            except (FetchError, ParseError) as error:
                failed += 1
                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=error.target or url,
                        mode=ScrapeMode.PUBLIC,
                        action=action.value,
                        retryable=isinstance(error, FetchError),
                    )
                )
                continue

            if action is PublicAction.CRAWL and depth < request.depth:
                discovered = self._discovery.from_html(
                    html,
                    base_url=url,
                    target=request.target_kind,
                    limit=request.max_nodes,
                )
                for candidate in discovered:
                    normalized = normalize_facebook_url(candidate)
                    if not normalized or normalized in visited or normalized in queued:
                        continue
                    if len(records) + failed + len(queue) >= request.max_nodes:
                        break
                    queue.append((normalized, depth + 1, url))
                    queued.add(normalized)

            if request.delay_seconds > 0 and queue:
                self._sleep(request.delay_seconds)

        return ScrapeResult(
            records=tuple(records),
            issues=tuple(issues),
            stats=ScrapeStats(
                requested=len(targets),
                discovered=initial_discovered + max(0, len(visited) - len(targets)),
                succeeded=len(records),
                failed=failed,
            ),
        )
```

- [ ] **Step 4: Run service and source crawl regression tests**

Run: `python -m pytest tests/integration/test_public_service.py -v`

Expected: all tests pass.

Run from `D:/project/fb/craw`: `python -m unittest test_scraper_cli.ScraperCliTests.test_crawl_respects_depth_and_deduplicates_urls test_scraper_cli.ScraperCliTests.test_crawl_respects_max_nodes -v`

Expected: both source tests remain passing.

- [ ] **Step 5: Commit the service**

```powershell
git add src/fb_crawl/services tests/integration/test_public_service.py
git commit -m "feat: orchestrate public scraping"
```

---

### Task 10: Add atomic CSV and JSON result exporters

**Files:**
- Create: `src/fb_crawl/exporters/__init__.py`
- Create: `src/fb_crawl/exporters/atomic.py`
- Create: `src/fb_crawl/exporters/csv.py`
- Create: `src/fb_crawl/exporters/json.py`
- Create: `tests/unit/exporters/test_csv_exporter.py`
- Create: `tests/unit/exporters/test_json_exporter.py`

**Interfaces:**
- Consumes: `ScrapeResult[PageRecord]` and `ExportError`.
- Produces: `write_csv(result, path) -> bool`, `write_json(result, path) -> bool`; both preserve an existing destination when the result contains no records and no issues.

- [ ] **Step 1: Write failing atomic exporter tests**

```python
# tests/unit/exporters/test_csv_exporter.py
import csv
from pathlib import Path

from fb_crawl.core.models import PageRecord, ScrapeResult, ScrapeStats
from fb_crawl.exporters.csv import write_csv


def test_empty_result_preserves_existing_csv(tmp_path: Path) -> None:
    destination = tmp_path / "pages.csv"
    destination.write_text("existing\n", encoding="utf-8")
    result = ScrapeResult(records=(), issues=(), stats=ScrapeStats(0, 0, 0, 0))
    assert write_csv(result, destination) is False
    assert destination.read_text(encoding="utf-8") == "existing\n"


def test_csv_writes_success_and_issue_rows(tmp_path: Path) -> None:
    from fb_crawl.core.models import PublicAction, ScrapeIssue, ScrapeMode

    result = ScrapeResult(
        records=(PageRecord(canonical_url="https://www.facebook.com/good", page_name="Good"),),
        issues=(
            ScrapeIssue(
                code="public_fetch_failed",
                message="Public fetch failed.",
                target="https://www.facebook.com/bad",
                mode=ScrapeMode.PUBLIC,
                action=PublicAction.PAGE.value,
                retryable=True,
            ),
        ),
        stats=ScrapeStats(2, 0, 1, 1),
    )
    destination = tmp_path / "pages.csv"
    assert write_csv(result, destination) is True
    rows = list(csv.DictReader(destination.open(encoding="utf-8-sig")))
    assert rows[0]["page_name"] == "Good"
    assert rows[1]["error_code"] == "public_fetch_failed"
```

```python
# tests/unit/exporters/test_json_exporter.py
import json
from pathlib import Path

from fb_crawl.core.models import PageRecord, ScrapeResult, ScrapeStats
from fb_crawl.exporters.json import write_json


def test_json_writes_full_result_envelope(tmp_path: Path) -> None:
    result = ScrapeResult(
        records=(PageRecord(canonical_url="https://www.facebook.com/good"),),
        issues=(),
        stats=ScrapeStats(1, 0, 1, 0),
    )
    destination = tmp_path / "pages.json"
    assert write_json(result, destination) is True
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["records"][0]["canonical_url"].endswith("/good")
    assert payload["stats"]["succeeded"] == 1
```

- [ ] **Step 2: Run the tests and verify exporters are missing**

Run: `python -m pytest tests/unit/exporters -v`

Expected: collection fails because exporter modules do not exist.

- [ ] **Step 3: Implement atomic text replacement**

```python
# src/fb_crawl/exporters/atomic.py
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from fb_crawl.core.exceptions import ExportError


@contextmanager
def atomic_text_writer(
    destination: Path,
    *,
    encoding: str,
    newline: str | None = None,
) -> Iterator[TextIO]:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding=encoding, newline=newline) as file:
            yield file
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    except OSError as error:
        raise ExportError(
            f"Cannot write output file {destination}.",
            target=str(destination),
        ) from error
    finally:
        if temporary.exists():
            temporary.unlink()
```

- [ ] **Step 4: Implement stable CSV serialization**

```python
# src/fb_crawl/exporters/csv.py
from __future__ import annotations

import csv
from pathlib import Path

from fb_crawl.core.models import ContactKind, PageRecord, ScrapeResult
from fb_crawl.exporters.atomic import atomic_text_writer


FIELDS = (
    "url", "page_name", "uid", "category", "website", "phone_numbers",
    "phone_sources", "depth", "source", "error_code", "error_message",
)


def _record_row(record: PageRecord) -> dict[str, object]:
    phones = [contact for contact in record.contacts if contact.kind is ContactKind.PHONE]
    return {
        "url": record.canonical_url,
        "page_name": record.page_name,
        "uid": record.uid,
        "category": record.category,
        "website": record.website,
        "phone_numbers": "; ".join(contact.value for contact in phones),
        "phone_sources": "; ".join(
            source for contact in phones for source in contact.sources
        ),
        "depth": record.depth,
        "source": record.discovery_source,
        "error_code": "",
        "error_message": "",
    }


def write_csv(result: ScrapeResult[PageRecord], path: Path) -> bool:
    if not result.records and not result.issues:
        return False
    rows = [_record_row(record) for record in result.records]
    rows.extend(
        {
            "url": issue.target or "",
            "page_name": "",
            "uid": "",
            "category": "",
            "website": "",
            "phone_numbers": "",
            "phone_sources": "",
            "depth": "",
            "source": "",
            "error_code": issue.code,
            "error_message": issue.message,
        }
        for issue in result.issues
    )
    with atomic_text_writer(Path(path), encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return True
```

- [ ] **Step 5: Implement full JSON envelope serialization**

```python
# src/fb_crawl/exporters/json.py
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from fb_crawl.core.models import PageRecord, ScrapeResult
from fb_crawl.exporters.atomic import atomic_text_writer


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def write_json(result: ScrapeResult[PageRecord], path: Path) -> bool:
    if not result.records and not result.issues:
        return False
    payload = {
        "records": _jsonable(result.records),
        "issues": _jsonable(result.issues),
        "stats": _jsonable(result.stats),
    }
    with atomic_text_writer(Path(path), encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return True
```

- [ ] **Step 6: Run focused exporter tests**

Run: `python -m pytest tests/unit/exporters -v`

Expected: all exporter tests pass.

- [ ] **Step 7: Commit exporters**

```powershell
git add src/fb_crawl/exporters tests/unit/exporters
git commit -m "feat: export public results atomically"
```

---

### Task 11: Build the public CLI composition root

**Files:**
- Create: `src/fb_crawl/cli/__init__.py`
- Create: `src/fb_crawl/cli/public.py`
- Create: `src/fb_crawl/cli/app.py`
- Modify: `pyproject.toml`
- Create: `tests/unit/cli/test_public_parser.py`
- Create: `tests/integration/test_public_cli.py`

**Interfaces:**
- Consumes: settings, public adapters, `PublicService`, exporters, and safe exceptions.
- Produces: `build_parser() -> argparse.ArgumentParser`; `main(argv: Sequence[str] | None = None) -> int`; installed `fb-crawl` command with `public page`, `public search`, and `public crawl`.

- [ ] **Step 1: Write failing parser tests**

```python
# tests/unit/cli/test_public_parser.py
import sys

from fb_crawl.cli.app import build_parser
from fb_crawl.cli.public import request_from_args
from fb_crawl.core.models import PublicAction, TargetKind


def test_page_command_builds_explicit_public_request() -> None:
    args = build_parser().parse_args(
        ["public", "page", "https://www.facebook.com/example", "--limit", "5"]
    )
    request = request_from_args(args)
    assert request.action is PublicAction.PAGE
    assert request.targets == ("https://www.facebook.com/example",)
    assert request.limit == 5
    assert request.target_kind is TargetKind.PAGES


def test_importing_public_cli_does_not_import_selenium() -> None:
    assert "selenium" not in sys.modules
```

- [ ] **Step 2: Write a failing CLI-to-export integration test**

```python
# tests/integration/test_public_cli.py
from pathlib import Path

from fb_crawl.cli.app import main
from fb_crawl.core.models import PageRecord, ScrapeResult, ScrapeStats


class FakeService:
    def run(self, request):
        return ScrapeResult(
            records=(PageRecord(canonical_url=request.targets[0], page_name="Example"),),
            issues=(),
            stats=ScrapeStats(1, 0, 1, 0),
        )


def test_public_page_command_writes_csv_without_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("fb_crawl.cli.public.build_public_service", lambda settings: FakeService())
    output = tmp_path / "pages.csv"
    exit_code = main(
        [
            "public", "page", "https://www.facebook.com/example",
            "--output", str(output),
        ]
    )
    assert exit_code == 0
    assert "Example" in output.read_text(encoding="utf-8-sig")
```

- [ ] **Step 3: Run tests and verify CLI modules are missing**

Run: `python -m pytest tests/unit/cli/test_public_parser.py tests/integration/test_public_cli.py -v`

Expected: collection fails because `fb_crawl.cli` does not exist.

- [ ] **Step 4: Implement the public parser and request mapping**

```python
# src/fb_crawl/cli/public.py
from __future__ import annotations

import argparse
from pathlib import Path

from fb_crawl.adapters.http.client import CurlHttpClient
from fb_crawl.adapters.http.contact_parser import ContactEnricher
from fb_crawl.adapters.http.discovery import PublicDiscovery
from fb_crawl.adapters.http.page_parser import PublicPageParser
from fb_crawl.config import Settings, load_settings
from fb_crawl.core.models import (
    PublicAction,
    ScrapeMode,
    ScrapeRequest,
    TargetKind,
)
from fb_crawl.exporters.csv import write_csv
from fb_crawl.exporters.json import write_json
from fb_crawl.services.public import PublicService


DEFAULT_FILENAMES = {
    PublicAction.PAGE: "pages",
    PublicAction.SEARCH: "pages",
    PublicAction.CRAWL: "pages",
}


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", choices=[item.value for item in TargetKind], default="pages")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--max-retries", type=int)


def add_public_parser(mode_subparsers) -> None:
    public_parser = mode_subparsers.add_parser("public", help="Use public HTTP scraping")
    actions = public_parser.add_subparsers(dest="action", required=True)

    page = actions.add_parser("page", help="Scrape direct public page/profile URLs")
    page.add_argument("urls", nargs="+")
    _common(page)

    search = actions.add_parser("search", help="Discover and scrape by keyword")
    search.add_argument("--keyword", required=True)
    _common(search)

    crawl = actions.add_parser(
        "crawl",
        help="Breadth-first crawl page/profile targets or a public group seed",
    )
    crawl.add_argument("urls", nargs="+")
    crawl.add_argument("--depth", type=int, default=1)
    crawl.add_argument("--max-nodes", type=int)
    _common(crawl)


def request_from_args(args: argparse.Namespace) -> ScrapeRequest:
    action = PublicAction(args.action)
    urls = tuple(getattr(args, "urls", ()))
    return ScrapeRequest(
        mode=ScrapeMode.PUBLIC,
        action=action,
        targets=urls,
        keyword=getattr(args, "keyword", None),
        target_kind=TargetKind(args.target),
        limit=args.limit,
        depth=getattr(args, "depth", 0),
        max_nodes=getattr(args, "max_nodes", None) or args.limit,
        delay_seconds=args.delay,
    )


def build_public_service(settings: Settings) -> PublicService:
    client = CurlHttpClient(settings)
    return PublicService(
        client,
        PublicDiscovery(client),
        PublicPageParser(),
        ContactEnricher(client),
    )


def execute_public(args: argparse.Namespace) -> int:
    settings = load_settings(
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    request = request_from_args(args)
    result = build_public_service(settings).run(request)
    output = args.output or (
        settings.output_dir
        / f"{DEFAULT_FILENAMES[PublicAction(args.action)]}.{args.format}"
    )
    written = write_csv(result, output) if args.format == "csv" else write_json(result, output)
    print(
        f"requested={result.stats.requested} succeeded={result.stats.succeeded} "
        f"failed={result.stats.failed} output={output if written else 'unchanged'}"
    )
    return 1 if result.has_failures else 0
```

- [ ] **Step 5: Implement the root parser and safe exit mapping**

```python
# src/fb_crawl/cli/app.py
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from fb_crawl.cli.public import add_public_parser, execute_public
from fb_crawl.core.exceptions import FbCrawlError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fb-crawl")
    modes = parser.add_subparsers(dest="mode", required=True)
    add_public_parser(modes)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.mode == "public":
            return execute_public(args)
        parser.error(f"Unsupported mode: {args.mode}")
    except FbCrawlError as error:
        print(error.safe_message, file=sys.stderr)
        return error.exit_code
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 2
```

Add the console entry point:

```toml
# append under [project] metadata in pyproject.toml
[project.scripts]
fb-crawl = "fb_crawl.cli.app:main"
```

- [ ] **Step 6: Reinstall editable metadata and run focused CLI tests**

Run: `python -m pip install -e ".[dev]"`

Expected: editable reinstall succeeds and creates the `fb-crawl` command.

Run: `python -m pytest tests/unit/cli/test_public_parser.py tests/integration/test_public_cli.py -v`

Expected: all CLI tests pass without network and `selenium` remains absent from imported modules.

- [ ] **Step 7: Run CLI help smoke checks**

Run: `fb-crawl --help`

Expected: exit `0` and help lists the `public` mode.

Run: `fb-crawl public --help`

Expected: exit `0` and help lists `page`, `search`, and `crawl`.

- [ ] **Step 8: Commit the public CLI**

```powershell
git add pyproject.toml src/fb_crawl/cli tests/unit/cli tests/integration/test_public_cli.py
git commit -m "feat: add public CLI commands"
```

---

### Task 12: Document, verify, and close the public phase

**Files:**
- Modify: `README.md`
- Create: `docs/public-cli.md`
- Create: `tests/unit/test_repository_safety.py`

**Interfaces:**
- Consumes: complete public CLI from Tasks 1–11.
- Produces: installation and command guide, documented privacy boundaries, repository-safety regression, and fresh full verification evidence.

- [ ] **Step 1: Write the failing repository-safety test**

```python
# tests/unit/test_repository_safety.py
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_and_secret_paths_are_ignored() -> None:
    for relative in (
        "runtime/output/pages.csv",
        "runtime/session.json",
        "runtime/geckodriver.log",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "-q", relative],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, relative


def test_source_projects_remain_outside_new_repository() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".facebook_session.json" not in tracked
    assert "results.csv" not in tracked
    assert ".ipynb" not in tracked
```

- [ ] **Step 2: Run the test before final documentation changes**

Run: `python -m pytest tests/unit/test_repository_safety.py -v`

Expected: test passes if Task 1 `.gitignore` is intact; if it fails, fix only the missing ignore entry and rerun until passing.

- [ ] **Step 3: Replace the minimal README with the public CLI guide**

The README must include these exact sections and commands:

````markdown
# fb-crawl

`fb-crawl` provides explicit public HTTP and authenticated browser modes. This phase implements the public CLI; it never reads a browser session or starts Selenium.

## Requirements

- Python 3.12+
- Access only to data you are authorized to collect

## Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Public commands

```powershell
fb-crawl public page https://www.facebook.com/example
fb-crawl public search --keyword "spa" --target pages --limit 20
fb-crawl public crawl https://www.facebook.com/example --depth 1 --max-nodes 20
fb-crawl public crawl https://www.facebook.com/groups/pythonvn --target all --depth 0
```

Use `--format json` for the full result envelope or `--output PATH` to select a destination. Default output is `runtime/output/pages.csv`.

## Exit codes

- `0`: run completed without target failures
- `1`: partial target failure; successful records remain available
- `2`: invalid input or configuration
- `4`: output could not be written safely

## Privacy and safety

Generated data is written under Git-ignored `runtime/`. Public mode does not use cookies or login credentials. The project does not bypass access controls, CAPTCHA, checkpoints, or two-factor authentication.

## Development checks

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
git diff --check
```
````

Move the detailed option descriptions and output schemas to `docs/public-cli.md`; keep README focused on onboarding.

- [ ] **Step 4: Run the complete offline test suite**

Run: `python -m pytest -q`

Expected: all tests pass, with zero failures and zero live network calls.

- [ ] **Step 5: Run syntax and dependency verification**

Run: `python -m compileall -q src tests`

Expected: exit `0` and no syntax errors.

Run: `python -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 6: Run installed CLI smoke checks**

Run: `fb-crawl --help`

Expected: exit `0`.

Run: `fb-crawl public page --help`

Expected: exit `0` and no Selenium/browser initialization.

Run: `fb-crawl public search --help`

Expected: exit `0` and keyword/target options are documented.

Run: `fb-crawl public crawl --help`

Expected: exit `0` and depth/max-node options are documented.

- [ ] **Step 7: Check repository cleanliness and source preservation**

Run: `git diff --check`

Expected: exit `0`.

Run: `git status --short`

Expected before the documentation commit: only `README.md`, `docs/public-cli.md`, and `tests/unit/test_repository_safety.py` are listed.

Run from `D:/project/fb/craw`: `python -m unittest discover -s . -p "test*.py" -v`

Expected: the existing 27 source tests pass and no source file is modified.

- [ ] **Step 8: Commit public phase documentation and safety checks**

```powershell
git add README.md docs/public-cli.md tests/unit/test_repository_safety.py
git commit -m "docs: complete public CLI phase"
```

- [ ] **Step 9: Record final evidence**

Run: `git status --short --branch`

Expected: clean branch with no untracked or modified files.

Run: `git log --oneline -12`

Expected: one focused commit for every completed task, ending with `docs: complete public CLI phase`.

---

## Phase Boundary

This plan ends when the public CLI is installable, offline-tested, documented, and cleanly committed. Do not add Selenium, session persistence, group-member extraction, post-comment extraction, Web UI, or API code while executing this plan. Create the authenticated CLI implementation plan from the approved design only after the public phase passes Task 12 verification.
