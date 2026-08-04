# Authenticated CLI with Selenium and Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit Selenium-backed `members`, `comments`, and `batch` authenticated commands that reuse a validated cookie session, return typed user records, and export them safely without changing public-mode behavior.

**Architecture:** Keep browser/session mechanics in optional adapters, orchestration in a browser-agnostic `AuthenticatedService`, and argument/file handling in a thin CLI composition root. One browser is created per command, one session is ensured per run, every browser loop is bounded, and the CLI closes the browser in `finally`.

**Tech Stack:** Python 3.12+, argparse, Selenium 4 Firefox driver, Beautiful Soup 4, optional openpyxl, pytest, dataclasses, protocols, atomic filesystem writes.

**Design spec:** [`docs/superpowers/specs/2026-08-05-authenticated-cli-design.md`](../specs/2026-08-05-authenticated-cli-design.md)

## Global Constraints

- Work only in `D:/project/fb/fb-crawl`; do not modify `D:/project/fb/Facebook-Data-Scraping-Tools` or copy any real session/output data from it.
- Preserve explicit modes: public scraping never reads a session, imports Selenium/Beautiful Soup, starts a browser, or falls back to authenticated mode.
- Use `selenium>=4.16,<5` and `beautifulsoup4>=4.12,<5` in the `browser` extra; use `openpyxl>=3.1,<4` in the `xlsx` extra.
- Keep Python support at `>=3.12` and keep `curl-cffi` plus `selectolax` as base dependencies.
- Never accept or persist Facebook credentials through CLI arguments, environment variables, settings, batch files, logs, exceptions, fixtures, or output.
- Never print or commit cookie values, raw session JSON, full Facebook HTML, proxy credentials, or real user data.
- Never bypass login, access controls, CAPTCHA, checkpoint, two-factor authentication, account recovery, or privacy settings; manual verification is operator-driven and bounded.
- Default session path is `runtime/session.json`; a path inside the repository must resolve under Git-ignored `runtime/`, while an external secret-mount path is allowed.
- Session files use a same-directory temporary file, `fsync`, atomic replace, and owner-only `0o600` permissions where supported.
- Every scroll, click, page-ready wait, normal-login wait, and manual-verification wait has an explicit finite bound.
- One Selenium browser is used per authenticated command and is closed in `finally` on success and every failure path.
- Automated tests are offline and synthetic; real Firefox/Facebook/login checks are manual-only.
- Followers, WebUI, API, database, scheduler, stealth/anti-detection behavior, and selector auto-repair remain outside this phase.
- Preserve the user's unrelated working-tree edits; stage only files named by the current task.

---

## Locked file map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Optional browser/XLSX dependencies only. |
| `src/fb_crawl/core/models.py` | Authenticated action, request step bound, immutable user record. |
| `src/fb_crawl/core/exceptions.py` | Stable session/navigation/parse errors and exit codes. |
| `src/fb_crawl/core/urls.py` | Pure authenticated target normalization/classification. |
| `src/fb_crawl/config.py` | Immutable browser settings, environment precedence, safe session-path validation. |
| `src/fb_crawl/adapters/browser/driver.py` | Firefox options, proxy preferences, document-ready wait, driver construction. |
| `src/fb_crawl/adapters/browser/session.py` | Cookie filtering, authentication validation, atomic restore/save. |
| `src/fb_crawl/adapters/browser/login.py` | Bounded login/manual-verification flow and session manager. |
| `src/fb_crawl/adapters/browser/user_parser.py` | Pure HTML-to-`UserRecord` parsing. |
| `src/fb_crawl/adapters/browser/members.py` | Bounded member-page scrolling. |
| `src/fb_crawl/adapters/browser/comments.py` | Bounded multilingual comment expansion. |
| `src/fb_crawl/services/authenticated.py` | Browser-agnostic members/comments/batch orchestration and deduplication. |
| `src/fb_crawl/exporters/atomic.py` | Shared same-directory atomic-path primitive. |
| `src/fb_crawl/exporters/users.py` | CSV/JSON/TXT/XLSX user-result dispatch and serialization. |
| `src/fb_crawl/cli/authenticated.py` | Authenticated parser, batch input, lazy composition, credentials prompt, browser ownership. |
| `src/fb_crawl/cli/app.py` | Register and dispatch the authenticated mode. |
| `docs/authenticated-cli.md`, `README.md` | Operator install, commands, security, troubleshooting, and manual checks. |

Task dependencies are linear where interfaces are consumed: 1 → 2/3/4/5 → 6 → 7/8 → 9 → 10 → 11 → 12. Tasks 2–5 may be developed independently after Task 1, but each task must still pass the full test suite before its commit.

### Task 1: Authenticated contracts, optional dependencies, and browser settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/fb_crawl/core/models.py`
- Modify: `src/fb_crawl/core/exceptions.py`
- Modify: `src/fb_crawl/core/__init__.py`
- Modify: `src/fb_crawl/config.py`
- Modify: `tests/unit/core/test_models.py`
- Modify: `tests/unit/core/test_exceptions.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: existing `ScrapeMode`, `PublicAction`, `ScrapeRequest`, `FbCrawlError`, and `ConfigurationError`.
- Produces: `AuthenticatedAction`, `UserRecord`, `ScrapeRequest.steps`, `SessionError`, `BrowserNavigationError`, `BrowserParseError`, `BrowserSettings`, `load_browser_settings(...)`, and `validate_session_path(...)`.

- [ ] **Step 1: Write failing contract and configuration tests**

Append these tests:

```python
# tests/unit/core/test_models.py
from fb_crawl.core.models import AuthenticatedAction, UserRecord


def test_authenticated_request_and_user_record_are_typed() -> None:
    request = ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.MEMBERS,
        targets=("https://www.facebook.com/groups/example/members",),
        steps=7,
    )
    record = UserRecord(
        user_id="123",
        name="Synthetic User",
        profile_url="https://www.facebook.com/profile.php?id=123",
        source="members",
        source_url=request.targets[0],
    )

    assert request.steps == 7
    assert record.user_id == "123"


def test_request_rejects_non_positive_authenticated_steps() -> None:
    with pytest.raises(ValueError, match="steps must be greater than 0"):
        ScrapeRequest(
            mode=ScrapeMode.AUTHENTICATED,
            action=AuthenticatedAction.COMMENTS,
            targets=("https://www.facebook.com/example/posts/1",),
            steps=0,
        )
```

```python
# tests/unit/core/test_exceptions.py
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    SessionError,
)


def test_authenticated_errors_have_stable_codes_and_exit_codes() -> None:
    assert SessionError("Session unavailable.").code == "authenticated_session_unavailable"
    assert SessionError("Session unavailable.").exit_code == 3
    assert BrowserNavigationError("Navigation failed.").code == "authenticated_navigation_failed"
    assert BrowserNavigationError("Navigation failed.").exit_code == 1
    assert BrowserParseError("Parse failed.").code == "authenticated_parse_failed"
    assert BrowserParseError("Parse failed.").exit_code == 1
```

```python
# tests/unit/test_config.py
from pathlib import Path

import pytest

from fb_crawl.config import load_browser_settings, validate_session_path
from fb_crawl.core.exceptions import ConfigurationError


def test_browser_settings_use_cli_then_environment_then_defaults(tmp_path: Path) -> None:
    settings = load_browser_settings(
        {
            "FB_CRAWL_HEADLESS": "true",
            "FB_CRAWL_PROXY": "socks5://127.0.0.1:9050",
            "FB_CRAWL_SESSION_PATH": "runtime/from-env.json",
            "FB_CRAWL_BROWSER_TIMEOUT_SECONDS": "41",
            "FB_CRAWL_VERIFICATION_TIMEOUT_SECONDS": "401",
        },
        headless=False,
        session_path=Path("runtime/from-cli.json"),
        repository_root=tmp_path,
    )

    assert settings.headless is False
    assert settings.proxy == "socks5://127.0.0.1:9050"
    assert settings.session_path == (tmp_path / "runtime/from-cli.json").resolve()
    assert settings.browser_timeout_seconds == 41.0
    assert settings.verification_timeout_seconds == 401.0


@pytest.mark.parametrize("value", ["sometimes", "2", ""])
def test_browser_settings_reject_malformed_boolean(value: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="FB_CRAWL_HEADLESS"):
        load_browser_settings(
            {"FB_CRAWL_HEADLESS": value},
            repository_root=tmp_path,
        )


def test_repo_local_session_must_stay_under_runtime(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="runtime"):
        validate_session_path(tmp_path / "session.json", repository_root=tmp_path)

    external = tmp_path.parent / "mounted-secret/session.json"
    assert validate_session_path(external, repository_root=tmp_path) == external.resolve()
```

- [ ] **Step 2: Run the focused tests and verify the missing contracts fail**

Run:

```powershell
python -m pytest tests/unit/core/test_models.py tests/unit/core/test_exceptions.py tests/unit/test_config.py -q
```

Expected: collection fails because `AuthenticatedAction`, `UserRecord`, authenticated errors, and browser settings do not exist.

- [ ] **Step 3: Add the optional dependency groups**

Replace the optional dependency table in `pyproject.toml` with:

```toml
[project.optional-dependencies]
browser = ["selenium>=4.16,<5", "beautifulsoup4>=4.12,<5"]
xlsx = ["openpyxl>=3.1,<4"]
dev = ["pytest"]
```

Install the development set before later browser-adapter tests:

```powershell
python -m pip install -e ".[browser,xlsx,dev]"
```

- [ ] **Step 4: Implement the domain contracts and stable errors**

Add to `src/fb_crawl/core/models.py` and update the `ScrapeRequest.action` annotation:

```python
class AuthenticatedAction(StrEnum):
    MEMBERS = "members"
    COMMENTS = "comments"
    BATCH = "batch"


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    name: str | None
    profile_url: str
    source: str
    source_url: str
```

```python
class ScrapeRequest:
    mode: ScrapeMode
    action: PublicAction | AuthenticatedAction | str
    targets: tuple[str, ...]
    keyword: str | None = None
    target_kind: TargetKind = TargetKind.PAGES
    limit: int = 20
    depth: int = 0
    max_nodes: int = 20
    delay_seconds: float = 0.0
    steps: int = 5

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be greater than 0")
        if self.depth < 0:
            raise ValueError("depth must be greater than or equal to 0")
        if self.max_nodes <= 0:
            raise ValueError("max_nodes must be greater than 0")
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be greater than or equal to 0")
        if self.steps <= 0:
            raise ValueError("steps must be greater than 0")
```

Add to `src/fb_crawl/core/exceptions.py`:

```python
class SessionError(FbCrawlError):
    code = "authenticated_session_unavailable"
    exit_code = 3


class BrowserNavigationError(FbCrawlError):
    code = "authenticated_navigation_failed"
    exit_code = 1


class BrowserParseError(FbCrawlError):
    code = "authenticated_parse_failed"
    exit_code = 1
```

Export all five new public types from `src/fb_crawl/core/__init__.py` by adding them to its imports and `__all__`.

- [ ] **Step 5: Implement immutable browser settings and path validation**

Append to `src/fb_crawl/config.py`:

```python
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    headless: bool = False
    proxy: str | None = None
    session_path: Path = Path("runtime/session.json")
    browser_timeout_seconds: float = 30.0
    verification_timeout_seconds: float = 300.0


def _boolean(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be a documented true/false value.")


def _positive_float(name: str, value: str | float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"{name} must be numeric.") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than 0.")
    return parsed


def validate_session_path(path: Path, *, repository_root: Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = repository_root / resolved
    resolved = resolved.resolve()
    repository = repository_root.resolve()
    runtime = (repository / "runtime").resolve()

    try:
        resolved.relative_to(repository)
    except ValueError:
        return resolved

    try:
        resolved.relative_to(runtime)
    except ValueError as error:
        raise ConfigurationError(
            "A repository-local session path must stay under runtime/."
        ) from error
    return resolved


def load_browser_settings(
    env: Mapping[str, str] | None = None,
    *,
    headless: bool | None = None,
    proxy: str | None = None,
    session_path: Path | None = None,
    browser_timeout_seconds: float | None = None,
    verification_timeout_seconds: float | None = None,
    repository_root: Path | None = None,
) -> BrowserSettings:
    values = os.environ if env is None else env
    root = Path.cwd() if repository_root is None else repository_root
    resolved_headless = (
        headless
        if headless is not None
        else _boolean("FB_CRAWL_HEADLESS", values.get("FB_CRAWL_HEADLESS", "false"))
    )
    resolved_proxy = proxy if proxy is not None else values.get("FB_CRAWL_PROXY")
    raw_session = session_path or Path(
        values.get("FB_CRAWL_SESSION_PATH", "runtime/session.json")
    )
    browser_timeout = _positive_float(
        "browser_timeout_seconds",
        browser_timeout_seconds
        if browser_timeout_seconds is not None
        else values.get("FB_CRAWL_BROWSER_TIMEOUT_SECONDS", "30"),
    )
    verification_timeout = _positive_float(
        "verification_timeout_seconds",
        verification_timeout_seconds
        if verification_timeout_seconds is not None
        else values.get("FB_CRAWL_VERIFICATION_TIMEOUT_SECONDS", "300"),
    )
    return BrowserSettings(
        headless=resolved_headless,
        proxy=resolved_proxy or None,
        session_path=validate_session_path(raw_session, repository_root=root),
        browser_timeout_seconds=browser_timeout,
        verification_timeout_seconds=verification_timeout,
    )
```

- [ ] **Step 6: Run focused and full tests**

Run:

```powershell
python -m pytest tests/unit/core/test_models.py tests/unit/core/test_exceptions.py tests/unit/test_config.py -q
python -m pytest -q
```

Expected: both commands pass; existing public model/config behavior remains unchanged.

- [ ] **Step 7: Commit only Task 1 files**

```powershell
git add -- pyproject.toml src/fb_crawl/core/models.py src/fb_crawl/core/exceptions.py src/fb_crawl/core/__init__.py src/fb_crawl/config.py tests/unit/core/test_models.py tests/unit/core/test_exceptions.py tests/unit/test_config.py
git diff --cached --check
git commit -m "feat: add authenticated core contracts"
```

### Task 2: Authenticated URL normalization and batch classification

**Files:**
- Modify: `src/fb_crawl/core/urls.py`
- Create: `tests/unit/core/test_authenticated_urls.py`

**Interfaces:**
- Consumes: `AuthenticatedAction` from Task 1 and existing Facebook host/path constants.
- Produces: `normalize_members_url(value) -> str | None`, `normalize_comments_url(value) -> str | None`, and `classify_authenticated_url(value) -> tuple[AuthenticatedAction, str] | None`.

- [ ] **Step 1: Write the failing URL-table tests**

```python
# tests/unit/core/test_authenticated_urls.py
import pytest

from fb_crawl.core.models import AuthenticatedAction
from fb_crawl.core.urls import (
    classify_authenticated_url,
    normalize_comments_url,
    normalize_members_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://facebook.com/groups/pythonvn",
            "https://www.facebook.com/groups/pythonvn/members",
        ),
        (
            "https://m.facebook.com/groups/123/members?ref=share#top",
            "https://www.facebook.com/groups/123/members",
        ),
    ],
)
def test_normalize_members_url(raw: str, expected: str) -> None:
    assert normalize_members_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://facebook.com/groups/10/posts/20?ref=share",
            "https://www.facebook.com/groups/10/posts/20",
        ),
        (
            "https://www.facebook.com/example/posts/20/",
            "https://www.facebook.com/example/posts/20",
        ),
        (
            "https://www.facebook.com/example/videos/30",
            "https://www.facebook.com/example/videos/30",
        ),
        (
            "https://www.facebook.com/reel/40",
            "https://www.facebook.com/reel/40",
        ),
        (
            "https://www.facebook.com/permalink.php?id=9&story_fbid=50&ref=x",
            "https://www.facebook.com/permalink.php?story_fbid=50&id=9",
        ),
        (
            "https://www.facebook.com/photo.php?fbid=60&id=9&set=x",
            "https://www.facebook.com/photo.php?fbid=60&id=9",
        ),
    ],
)
def test_normalize_comments_url(raw: str, expected: str) -> None:
    assert normalize_comments_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.facebook.com/places/Ha-Noi/123",
        "https://www.facebook.com/login",
        "https://www.facebook.com/checkpoint/",
        "https://www.facebook.com/example",
        "https://example.test/groups/1/members",
    ],
)
def test_authenticated_url_helpers_reject_unsupported_targets(raw: str) -> None:
    assert normalize_members_url(raw) is None
    assert normalize_comments_url(raw) is None
    assert classify_authenticated_url(raw) is None


def test_batch_classifier_returns_action_and_normalized_url() -> None:
    assert classify_authenticated_url("https://facebook.com/groups/1") == (
        AuthenticatedAction.MEMBERS,
        "https://www.facebook.com/groups/1/members",
    )
    assert classify_authenticated_url("https://facebook.com/acme/posts/2") == (
        AuthenticatedAction.COMMENTS,
        "https://www.facebook.com/acme/posts/2",
    )
```

- [ ] **Step 2: Run the focused test and confirm imports fail**

```powershell
python -m pytest tests/unit/core/test_authenticated_urls.py -q
```

Expected: collection fails because the three authenticated URL helpers do not exist.

- [ ] **Step 3: Implement exact route validation and canonicalization**

Add `urlencode` to the existing `urllib.parse` imports, import `AuthenticatedAction`, and append:

```python
AUTHENTICATED_ID = re.compile(r"[A-Za-z0-9._-]+")


def _facebook_parts(value: str | None) -> tuple[list[str], dict[str, list[str]]] | None:
    if not value:
        return None
    parsed = urlparse(_absolute_candidate(value, None))
    host = parsed.netloc.lower().split(":")[0]
    if host not in FACEBOOK_HOSTS:
        return None
    return [part for part in parsed.path.split("/") if part], parse_qs(parsed.query)


def _valid_authenticated_id(value: str) -> bool:
    return AUTHENTICATED_ID.fullmatch(value) is not None


def normalize_members_url(value: str | None) -> str | None:
    parsed = _facebook_parts(value)
    if parsed is None:
        return None
    parts, _ = parsed
    if len(parts) not in {2, 3} or parts[0].lower() != "groups":
        return None
    if not _valid_authenticated_id(parts[1]):
        return None
    if len(parts) == 3 and parts[2].lower() != "members":
        return None
    return f"https://www.facebook.com/groups/{parts[1]}/members"


def normalize_comments_url(value: str | None) -> str | None:
    parsed = _facebook_parts(value)
    if parsed is None:
        return None
    parts, query = parsed
    lowered = [part.lower() for part in parts]

    if (
        len(parts) == 4
        and lowered[0] == "groups"
        and lowered[2] == "posts"
        and all(_valid_authenticated_id(item) for item in (parts[1], parts[3]))
    ):
        return f"https://www.facebook.com/groups/{parts[1]}/posts/{parts[3]}"

    if (
        len(parts) == 3
        and lowered[1] in {"posts", "videos"}
        and lowered[0] not in FACEBOOK_INTERNAL_PATHS
        and all(_valid_authenticated_id(item) for item in (parts[0], parts[2]))
    ):
        return f"https://www.facebook.com/{parts[0]}/{lowered[1]}/{parts[2]}"

    if len(parts) == 2 and lowered[0] == "reel" and _valid_authenticated_id(parts[1]):
        return f"https://www.facebook.com/reel/{parts[1]}"

    if len(parts) == 1 and lowered[0] == "permalink.php":
        story_id = query.get("story_fbid", [""])[0]
        owner_id = query.get("id", [""])[0]
        if not _valid_authenticated_id(story_id):
            return None
        values = [("story_fbid", story_id)]
        if _valid_authenticated_id(owner_id):
            values.append(("id", owner_id))
        return "https://www.facebook.com/permalink.php?" + urlencode(values)

    if len(parts) == 1 and lowered[0] == "photo.php":
        photo_id = query.get("fbid", [""])[0]
        owner_id = query.get("id", [""])[0]
        if not _valid_authenticated_id(photo_id):
            return None
        values = [("fbid", photo_id)]
        if _valid_authenticated_id(owner_id):
            values.append(("id", owner_id))
        return "https://www.facebook.com/photo.php?" + urlencode(values)
    return None


def classify_authenticated_url(
    value: str | None,
) -> tuple[AuthenticatedAction, str] | None:
    members = normalize_members_url(value)
    if members is not None:
        return AuthenticatedAction.MEMBERS, members
    comments = normalize_comments_url(value)
    if comments is not None:
        return AuthenticatedAction.COMMENTS, comments
    return None
```

- [ ] **Step 4: Run URL tests and the existing public URL suite**

```powershell
python -m pytest tests/unit/core/test_authenticated_urls.py tests/unit/core/test_urls.py -q
python -m pytest -q
```

Expected: authenticated and public URL tests pass; existing Places rejection remains effective.

- [ ] **Step 5: Commit only URL-rule files**

```powershell
git add -- src/fb_crawl/core/urls.py tests/unit/core/test_authenticated_urls.py
git diff --cached --check
git commit -m "feat: classify authenticated Facebook targets"
```

### Task 3: Pure authenticated user parser

**Files:**
- Create: `src/fb_crawl/adapters/browser/__init__.py`
- Create: `src/fb_crawl/adapters/browser/user_parser.py`
- Create: `tests/fixtures/authenticated/members.html`
- Create: `tests/fixtures/authenticated/comments.html`
- Create: `tests/unit/adapters/browser/test_user_parser.py`

**Interfaces:**
- Consumes: `UserRecord` from Task 1 and Beautiful Soup from the `browser` extra.
- Produces: `UserParser.parse(html: str, *, source: str, source_url: str) -> tuple[UserRecord, ...]`.

- [ ] **Step 1: Add synthetic HTML fixtures**

Create `tests/fixtures/authenticated/members.html`:

```html
<!doctype html>
<html><body>
  <a href="/groups/100/user/200/?ref=group_members">Member One</a>
  <a href="/profile.php?id=201&amp;ref=bookmarks" aria-label="Member Two"></a>
  <a href="/groups/100/user/200/?comment_id=9">Duplicate Member One</a>
  <a href="/groups/100/admin_activities">Admin activity</a>
</body></html>
```

Create `tests/fixtures/authenticated/comments.html`:

```html
<!doctype html>
<html><body>
  <a class="_a6hd" href="/synthetic.handle?comment_id=300">Handle Name</a>
  <a href="https://www.facebook.com/user/202/?comment_id=301" aria-label="User 202"></a>
  <a class="_a6hd" href="/synthetic.handle?comment_id=302">Trả lời</a>
  <a href="/share/1">Share</a>
</body></html>
```

- [ ] **Step 2: Write failing parser tests**

```python
# tests/unit/adapters/browser/test_user_parser.py
from pathlib import Path

from fb_crawl.adapters.browser.user_parser import UserParser

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/authenticated"


def test_parser_extracts_member_identity_forms_and_deduplicates() -> None:
    records = UserParser().parse(
        (FIXTURES / "members.html").read_text(encoding="utf-8"),
        source="members",
        source_url="https://www.facebook.com/groups/100/members",
    )

    assert [record.user_id for record in records] == ["200", "201"]
    assert records[0].profile_url == "https://www.facebook.com/profile.php?id=200"
    assert records[1].name == "Member Two"


def test_parser_accepts_profile_class_handles_and_filters_action_labels() -> None:
    records = UserParser().parse(
        (FIXTURES / "comments.html").read_text(encoding="utf-8"),
        source="comments",
        source_url="https://www.facebook.com/example/posts/1",
    )

    assert [(record.user_id, record.name) for record in records] == [
        ("synthetic.handle", "Handle Name"),
        ("202", "User 202"),
    ]
    assert all("comment_id" not in record.profile_url for record in records)


def test_parser_keeps_a_valid_identity_when_name_is_unavailable() -> None:
    records = UserParser().parse(
        '<a href="/user/203/"></a>',
        source="comments",
        source_url="https://www.facebook.com/example/posts/1",
    )

    assert [(record.user_id, record.name) for record in records] == [("203", None)]
```

- [ ] **Step 3: Run the parser test and confirm the adapter is missing**

```powershell
python -m pytest tests/unit/adapters/browser/test_user_parser.py -q
```

Expected: collection fails because `fb_crawl.adapters.browser.user_parser` does not exist.

- [ ] **Step 4: Implement pure profile identity parsing**

Create `src/fb_crawl/adapters/browser/__init__.py` as an empty package file, then create `user_parser.py`:

```python
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from fb_crawl.core.models import UserRecord
from fb_crawl.core.urls import FACEBOOK_INTERNAL_PATHS

FACEBOOK_BASE = "https://www.facebook.com"
PROFILE_LINK_CLASS = "_a6hd"
IDENTITY = re.compile(r"[A-Za-z0-9._-]+")
ACTION_LABELS = frozenset(
    item.casefold()
    for item in (
        "Reply",
        "Share",
        "Like",
        "Trả lời",
        "Thích",
        "Chia sẻ",
    )
)


def _name(anchor) -> str | None:
    visible = " ".join(anchor.stripped_strings).strip()
    candidate = visible or str(anchor.get("aria-label") or "").strip()
    return candidate or None


def _is_action_label(name: str | None) -> bool:
    return name is not None and name.casefold() in ACTION_LABELS


def _identity(anchor) -> tuple[str, str] | None:
    href = str(anchor.get("href") or "").replace("\\/", "/")
    if not href:
        return None
    absolute = urljoin(FACEBOOK_BASE, href)
    parsed = urlparse(absolute)
    if parsed.netloc.lower().split(":")[0] not in {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "mbasic.facebook.com",
        "web.facebook.com",
    }:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    if len(parts) >= 4 and parts[0].lower() == "groups" and parts[2].lower() == "user":
        user_id = parts[3]
    elif len(parts) >= 2 and parts[0].lower() == "user":
        user_id = parts[1]
    elif len(parts) == 1 and parts[0].lower() == "profile.php":
        user_id = query.get("id", [""])[0]
    elif (
        len(parts) == 1
        and PROFILE_LINK_CLASS in anchor.get("class", [])
        and parts[0].lower() not in FACEBOOK_INTERNAL_PATHS
    ):
        user_id = parts[0]
    else:
        return None

    if IDENTITY.fullmatch(user_id) is None:
        return None
    return user_id, f"{FACEBOOK_BASE}/profile.php?id={user_id}" if user_id.isdigit() else f"{FACEBOOK_BASE}/{user_id}"


class UserParser:
    def parse(
        self,
        html: str,
        *,
        source: str,
        source_url: str,
    ) -> tuple[UserRecord, ...]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[UserRecord] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            identity = _identity(anchor)
            name = _name(anchor)
            if identity is None or _is_action_label(name):
                continue
            user_id, profile_url = identity
            if user_id in seen:
                continue
            seen.add(user_id)
            records.append(
                UserRecord(
                    user_id=user_id,
                    name=name,
                    profile_url=profile_url,
                    source=source,
                    source_url=source_url,
                )
            )
        return tuple(records)
```

- [ ] **Step 5: Run focused and full offline tests**

```powershell
python -m pytest tests/unit/adapters/browser/test_user_parser.py -q
python -m pytest -q
```

Expected: synthetic numeric/handle identities pass, action links are absent, and no live browser/network is used.

- [ ] **Step 6: Commit parser and synthetic fixtures**

```powershell
git add -- src/fb_crawl/adapters/browser/__init__.py src/fb_crawl/adapters/browser/user_parser.py tests/fixtures/authenticated/members.html tests/fixtures/authenticated/comments.html tests/unit/adapters/browser/test_user_parser.py
git diff --cached --check
git commit -m "feat: parse authenticated user records"
```

### Task 4: Firefox driver factory and bounded document-ready wait

**Files:**
- Create: `src/fb_crawl/adapters/browser/driver.py`
- Create: `tests/unit/adapters/browser/test_driver.py`

**Interfaces:**
- Consumes: `BrowserSettings` and `ConfigurationError` from Task 1.
- Produces: `build_firefox_options(settings)`, `wait_for_document_ready(browser, timeout_seconds)`, and `create_firefox_driver(settings)`.

- [ ] **Step 1: Write failing Firefox option tests**

```python
# tests/unit/adapters/browser/test_driver.py
import pytest

from fb_crawl.adapters.browser.driver import build_firefox_options
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import ConfigurationError


def test_firefox_options_include_headless_window_and_http_proxy() -> None:
    options = build_firefox_options(
        BrowserSettings(headless=True, proxy="http://127.0.0.1:8080")
    )

    assert "-headless" in options.arguments
    assert "--width=1920" in options.arguments
    assert options.preferences["network.proxy.type"] == 1
    assert options.preferences["network.proxy.http"] == "127.0.0.1"
    assert options.preferences["network.proxy.http_port"] == 8080


def test_firefox_options_include_socks_proxy_version() -> None:
    options = build_firefox_options(
        BrowserSettings(proxy="socks5://127.0.0.1:9050")
    )

    assert options.preferences["network.proxy.socks"] == "127.0.0.1"
    assert options.preferences["network.proxy.socks_port"] == 9050
    assert options.preferences["network.proxy.socks_version"] == 5


@pytest.mark.parametrize(
    "proxy",
    ["ftp://127.0.0.1:21", "http://user:secret@127.0.0.1:8080", "http://missing-port"],
)
def test_firefox_options_reject_unsupported_or_credentialed_proxy(proxy: str) -> None:
    with pytest.raises(ConfigurationError, match="proxy"):
        build_firefox_options(BrowserSettings(proxy=proxy))
```

- [ ] **Step 2: Run the focused test and confirm the driver module is missing**

```powershell
python -m pytest tests/unit/adapters/browser/test_driver.py -q
```

Expected: collection fails because `fb_crawl.adapters.browser.driver` does not exist.

- [ ] **Step 3: Implement Firefox settings, one bounded readiness wait, and safe startup errors**

Create `src/fb_crawl/adapters/browser/driver.py`:

```python
from __future__ import annotations

from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, ConfigurationError


def _apply_proxy(options: Options, value: str) -> None:
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.hostname is None or parsed.port is None:
        raise ConfigurationError("Authenticated proxy URLs are not supported.")
    options.set_preference("network.proxy.type", 1)
    if parsed.scheme in {"http", "https"}:
        options.set_preference("network.proxy.http", parsed.hostname)
        options.set_preference("network.proxy.http_port", parsed.port)
        options.set_preference("network.proxy.ssl", parsed.hostname)
        options.set_preference("network.proxy.ssl_port", parsed.port)
    elif parsed.scheme in {"socks4", "socks5"}:
        options.set_preference("network.proxy.socks", parsed.hostname)
        options.set_preference("network.proxy.socks_port", parsed.port)
        options.set_preference("network.proxy.socks_version", int(parsed.scheme[-1]))
        options.set_preference("network.proxy.socks_remote_dns", True)
    else:
        raise ConfigurationError("The proxy scheme must be http, https, socks4, or socks5.")


def build_firefox_options(settings: BrowserSettings) -> Options:
    options = Options()
    options.add_argument("--width=1920")
    options.add_argument("--height=1080")
    options.set_preference("dom.webnotifications.enabled", False)
    options.set_preference("geo.enabled", False)
    if settings.headless:
        options.add_argument("-headless")
    if settings.proxy:
        _apply_proxy(options, settings.proxy)
    return options


def wait_for_document_ready(browser, timeout_seconds: float) -> None:
    try:
        WebDriverWait(browser, timeout_seconds).until(
            lambda driver: driver.execute_script("return document.readyState") == "complete"
        )
    except WebDriverException as error:
        raise BrowserNavigationError("Facebook page readiness timed out.") from error


def create_firefox_driver(settings: BrowserSettings):
    try:
        return webdriver.Firefox(options=build_firefox_options(settings))
    except WebDriverException as error:
        raise ConfigurationError(
            "Could not start Firefox. Install Firefox and the browser extra."
        ) from error
```

- [ ] **Step 4: Add and run a bounded-wait unit test using a fake wait**

Append to `tests/unit/adapters/browser/test_driver.py`:

```python
def test_document_ready_wait_uses_one_explicit_timeout(monkeypatch) -> None:
    observed: list[float] = []

    class FakeWait:
        def __init__(self, browser, timeout: float) -> None:
            observed.append(timeout)

        def until(self, predicate) -> None:
            assert predicate(type("Browser", (), {"execute_script": lambda self, script: "complete"})())

    monkeypatch.setattr("fb_crawl.adapters.browser.driver.WebDriverWait", FakeWait)

    from fb_crawl.adapters.browser.driver import wait_for_document_ready

    wait_for_document_ready(object(), 12.5)
    assert observed == [12.5]
```

Run:

```powershell
python -m pytest tests/unit/adapters/browser/test_driver.py -q
python -m pytest -q
```

Expected: all tests pass without starting Firefox.

- [ ] **Step 5: Commit only the driver factory**

```powershell
git add -- src/fb_crawl/adapters/browser/driver.py tests/unit/adapters/browser/test_driver.py
git diff --cached --check
git commit -m "feat: add bounded Firefox driver factory"
```

### Task 5: Validated and atomic cookie session store

**Files:**
- Create: `src/fb_crawl/adapters/browser/session.py`
- Create: `tests/unit/adapters/browser/test_session.py`

**Interfaces:**
- Consumes: `SessionError` from Task 1 and a Selenium-compatible browser object exposing `get`, `get_cookies`, `add_cookie`, `refresh`, and `current_url`.
- Produces: `is_authenticated(browser) -> bool` and `SessionStore(path).restore(browser) -> bool` / `.save(browser) -> None`.

- [ ] **Step 1: Write failing session tests with synthetic cookies**

```python
# tests/unit/adapters/browser/test_session.py
import json
import os
import stat
from pathlib import Path

import pytest

from fb_crawl.adapters.browser.session import SessionStore, is_authenticated
from fb_crawl.core.exceptions import SessionError


class FakeBrowser:
    def __init__(self, cookies=None, current_url="https://www.facebook.com/") -> None:
        self.cookies = list(cookies or [])
        self.current_url = current_url
        self.added: list[dict[str, object]] = []
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)

    def get_cookies(self):
        return list(self.cookies or self.added)

    def add_cookie(self, cookie) -> None:
        self.added.append(cookie)

    def refresh(self) -> None:
        self.cookies = list(self.added)


def test_authentication_requires_c_user_and_non_verification_url() -> None:
    assert is_authenticated(FakeBrowser([{"name": "c_user", "value": "100"}]))
    assert not is_authenticated(FakeBrowser([], "https://www.facebook.com/"))
    assert not is_authenticated(
        FakeBrowser(
            [{"name": "c_user", "value": "100"}],
            "https://www.facebook.com/checkpoint/123",
        )
    )


def test_restore_filters_cookie_fields_and_revalidates(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "c_user",
                    "value": "100",
                    "domain": ".facebook.com",
                    "sameSite": "Lax",
                    "unsupported": "secret",
                },
                {"name": 7, "value": "invalid"},
            ]
        ),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    assert SessionStore(path).restore(browser) is True
    assert browser.visited == ["https://www.facebook.com/"]
    assert browser.added == [
        {
            "name": "c_user",
            "value": "100",
            "domain": ".facebook.com",
            "sameSite": "Lax",
        }
    ]


@pytest.mark.parametrize("content", ["not-json", "{}", "[1, null]"])
def test_restore_treats_malformed_content_as_unavailable(
    content: str, tmp_path: Path
) -> None:
    path = tmp_path / "session.json"
    path.write_text(content, encoding="utf-8")

    assert SessionStore(path).restore(FakeBrowser()) is False


def test_save_is_atomic_owner_only_and_requires_authentication(tmp_path: Path) -> None:
    path = tmp_path / "nested/session.json"
    authenticated = FakeBrowser([{"name": "c_user", "value": "100"}])
    SessionStore(path).save(authenticated)

    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "c_user"
    assert not path.with_name("session.json.tmp").exists()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    with pytest.raises(SessionError, match="valid authenticated session"):
        SessionStore(tmp_path / "other.json").save(FakeBrowser())
```

- [ ] **Step 2: Run the session tests and verify the module is missing**

```powershell
python -m pytest tests/unit/adapters/browser/test_session.py -q
```

Expected: collection fails because the session adapter does not exist.

- [ ] **Step 3: Implement cookie sanitization and authentication validation**

Create `src/fb_crawl/adapters/browser/session.py`:

```python
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from fb_crawl.core.exceptions import SessionError

FACEBOOK_HOME = "https://www.facebook.com/"
COOKIE_FIELDS = (
    "name",
    "value",
    "path",
    "domain",
    "secure",
    "httpOnly",
    "expiry",
    "sameSite",
)
VALID_SAME_SITE = frozenset({"Strict", "Lax", "None"})
BLOCKED_AUTH_PATHS = ("/login", "/checkpoint", "/two_step_verification")


def is_authenticated(browser) -> bool:
    path = urlparse(str(browser.current_url or "")).path.lower()
    if any(path.startswith(prefix) for prefix in BLOCKED_AUTH_PATHS):
        return False
    return any(
        cookie.get("name") == "c_user" and bool(cookie.get("value"))
        for cookie in browser.get_cookies()
        if isinstance(cookie, Mapping)
    )


def _compatible_cookie(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    if not isinstance(value.get("name"), str) or not isinstance(value.get("value"), str):
        return None
    cookie = {field: value[field] for field in COOKIE_FIELDS if field in value}
    if cookie.get("sameSite") not in VALID_SAME_SITE:
        cookie.pop("sameSite", None)
    if "expiry" in cookie:
        try:
            cookie["expiry"] = int(cookie["expiry"])
        except (TypeError, ValueError):
            cookie.pop("expiry")
    return cookie
```

- [ ] **Step 4: Implement secret-safe restore and atomic save**

Append to `session.py`:

```python
class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def restore(self, browser) -> bool:
        if not self.path.is_file():
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, list):
            return False
        cookies = [cookie for item in payload if (cookie := _compatible_cookie(item))]
        if not cookies:
            return False
        try:
            browser.get(FACEBOOK_HOME)
            for cookie in cookies:
                browser.add_cookie(cookie)
            browser.refresh()
            return is_authenticated(browser)
        except Exception:
            return False

    def save(self, browser) -> None:
        if not is_authenticated(browser):
            raise SessionError("Cannot save without a valid authenticated session.")
        cookies = [
            cookie
            for item in browser.get_cookies()
            if (cookie := _compatible_cookie(item)) is not None
        ]
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(cookies, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except (OSError, TypeError, ValueError) as error:
            raise SessionError(f"Cannot persist session file {self.path}.") from error
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
```

The broad exception in `restore` is deliberate: a stale/incompatible Selenium cookie is equivalent to an unavailable session. It returns only `False` and never includes cookie content in an error.

- [ ] **Step 5: Run focused, secret-safety, and full tests**

```powershell
python -m pytest tests/unit/adapters/browser/test_session.py tests/unit/test_repository_safety.py -q
python -m pytest -q
```

Expected: session tests pass, no temporary file remains, and repository ignore checks remain green.

- [ ] **Step 6: Commit only the session store**

```powershell
git add -- src/fb_crawl/adapters/browser/session.py tests/unit/adapters/browser/test_session.py
git diff --cached --check
git commit -m "feat: persist validated browser sessions"
```

### Task 6: Bounded login and session manager

**Files:**
- Create: `src/fb_crawl/adapters/browser/login.py`
- Create: `tests/unit/adapters/browser/test_login.py`

**Interfaces:**
- Consumes: `BrowserSettings`, `SessionError`, `SessionStore`, and `is_authenticated`.
- Produces: `login_to_facebook(...)` and `SessionManager.ensure_authenticated(browser)` / `.assert_authenticated(browser)`.

- [ ] **Step 1: Write failing session-manager tests**

```python
# tests/unit/adapters/browser/test_login.py
import pytest

from fb_crawl.adapters.browser.login import SessionManager, login_to_facebook
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import SessionError


class FakeStore:
    def __init__(self, restored: bool) -> None:
        self.restored = restored
        self.saved = 0

    def restore(self, browser) -> bool:
        return self.restored

    def save(self, browser) -> None:
        self.saved += 1


def test_manager_reuses_restored_session_without_credentials() -> None:
    store = FakeStore(restored=True)
    manager = SessionManager(
        store,
        BrowserSettings(headless=True),
        credentials_provider=lambda: pytest.fail("credentials must not be requested"),
    )

    manager.ensure_authenticated(object())
    assert store.saved == 0


def test_headless_manager_fails_without_prompt_when_restore_fails() -> None:
    manager = SessionManager(
        FakeStore(restored=False),
        BrowserSettings(headless=True),
        credentials_provider=lambda: pytest.fail("headless mode must not prompt"),
    )

    with pytest.raises(SessionError, match="interactive"):
        manager.ensure_authenticated(object())


def test_interactive_manager_logs_in_then_saves() -> None:
    store = FakeStore(restored=False)
    calls: list[tuple[str, str]] = []
    manager = SessionManager(
        store,
        BrowserSettings(headless=False),
        credentials_provider=lambda: ("synthetic@example.test", "not-a-real-password"),
        login_func=lambda browser, email, password, **kwargs: calls.append((email, password)),
    )

    manager.ensure_authenticated(object())
    assert calls == [("synthetic@example.test", "not-a-real-password")]
    assert store.saved == 1
```

- [ ] **Step 2: Write failing bounded verification tests**

Append to `tests/unit/adapters/browser/test_login.py`:

```python
class Element:
    def __init__(self, on_click=lambda: None) -> None:
        self.values: list[str] = []
        self.clicks = 0
        self.on_click = on_click

    def clear(self) -> None:
        self.values.clear()

    def send_keys(self, value: str) -> None:
        self.values.append(value)

    def click(self) -> None:
        self.clicks += 1
        self.on_click()

    def is_displayed(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True


class LoginBrowser:
    def __init__(self) -> None:
        self.current_url = "https://www.facebook.com/login"
        self.cookies: list[dict[str, str]] = []
        self.elements = {
            ("name", "email"): Element(),
            ("name", "pass"): Element(),
        }
        self.submit_elements = [
            Element(),
            Element(
                lambda: setattr(
                    self,
                    "current_url",
                    "https://www.facebook.com/checkpoint/",
                )
            ),
        ]
        self.submit_lookups = 0

    def get(self, url: str) -> None:
        self.current_url = url

    def get_cookies(self):
        return self.cookies

    def find_element(self, by: str, value: str):
        if value == "form#login_form [role='button']":
            element = self.submit_elements[self.submit_lookups]
            self.submit_lookups += 1
            return element
        return self.elements[(by, value)]


def test_login_reacquires_submit_and_allows_bounded_manual_verification() -> None:
    browser = LoginBrowser()
    clock = iter([0.0, 0.1, 0.2, 0.3, 0.4])

    def sleep_and_complete(seconds: float) -> None:
        browser.current_url = "https://www.facebook.com/"
        browser.cookies = [{"name": "c_user", "value": "100"}]

    login_to_facebook(
        browser,
        "synthetic@example.test",
        "not-a-real-password",
        settings=BrowserSettings(
            headless=False,
            browser_timeout_seconds=0.15,
            verification_timeout_seconds=1.0,
        ),
        sleep_func=sleep_and_complete,
        monotonic_func=lambda: next(clock),
        print_func=lambda message: None,
    )

    assert browser.submit_lookups == 2
    assert browser.submit_elements[1].clicks == 1


def test_headless_checkpoint_fails_without_manual_polling(capsys) -> None:
    browser = LoginBrowser()

    with pytest.raises(SessionError, match="manual verification") as captured:
        login_to_facebook(
            browser,
            "synthetic@example.test",
            "not-a-real-password",
            settings=BrowserSettings(headless=True, browser_timeout_seconds=0.01),
            sleep_func=lambda seconds: None,
            monotonic_func=iter([0.0, 1.0]).__next__,
            print_func=lambda message: None,
        )
    output = capsys.readouterr()
    combined = captured.value.safe_message + output.out + output.err
    assert "synthetic@example.test" not in combined
    assert "not-a-real-password" not in combined
```

- [ ] **Step 3: Run the login tests and confirm the module is missing**

```powershell
python -m pytest tests/unit/adapters/browser/test_login.py -q
```

Expected: collection fails because the login adapter does not exist.

- [ ] **Step 4: Implement stable login selectors and bounded polling**

Create `src/fb_crawl/adapters/browser/login.py`:

```python
from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from fb_crawl.adapters.browser.session import SessionStore, is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import SessionError

LOGIN_URL = "https://www.facebook.com/login"
VERIFICATION_PATHS = ("/checkpoint", "/two_step_verification")


def _needs_manual_verification(browser) -> bool:
    path = urlparse(str(browser.current_url or "")).path.lower()
    return any(path.startswith(prefix) for prefix in VERIFICATION_PATHS)


def _wait_for_resolution(
    browser,
    timeout_seconds: float,
    *,
    sleep_func: Callable[[float], None],
    monotonic_func: Callable[[], float],
) -> str:
    deadline = monotonic_func() + timeout_seconds
    while monotonic_func() < deadline:
        if is_authenticated(browser):
            return "authenticated"
        if _needs_manual_verification(browser):
            return "verification"
        sleep_func(min(0.5, timeout_seconds))
    if is_authenticated(browser):
        return "authenticated"
    if _needs_manual_verification(browser):
        return "verification"
    return "timeout"


def _wait_until_authenticated(
    browser,
    timeout_seconds: float,
    *,
    sleep_func: Callable[[float], None],
    monotonic_func: Callable[[], float],
) -> bool:
    deadline = monotonic_func() + timeout_seconds
    while monotonic_func() < deadline:
        if is_authenticated(browser):
            return True
        sleep_func(min(0.5, timeout_seconds))
    return is_authenticated(browser)


def _login_flow(
    browser,
    email: str,
    password: str,
    *,
    settings: BrowserSettings,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
    print_func: Callable[[str], None] = print,
    wait_factory=WebDriverWait,
) -> None:
    browser.get(LOGIN_URL)
    wait = wait_factory(browser, settings.browser_timeout_seconds)
    email_input = wait.until(EC.presence_of_element_located((By.NAME, "email")))
    password_input = wait.until(EC.presence_of_element_located((By.NAME, "pass")))
    submit_locator = (By.CSS_SELECTOR, "form#login_form [role='button']")
    wait.until(EC.element_to_be_clickable(submit_locator))
    email_input.clear()
    email_input.send_keys(email)
    password_input.clear()
    password_input.send_keys(password)
    submit = wait.until(EC.element_to_be_clickable(submit_locator))
    submit.click()

    resolution = _wait_for_resolution(
        browser,
        settings.browser_timeout_seconds,
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
    )
    if resolution == "authenticated":
        return
    if resolution == "timeout":
        raise SessionError("Authenticated login did not complete before its timeout.")
    if settings.headless:
        raise SessionError(
            "Authenticated login requires manual verification; rerun with --no-headless."
        )

    print_func(
        "Complete any Facebook checkpoint or two-factor step in the open browser; "
        "fb-crawl will wait for the bounded verification timeout."
    )
    if _wait_until_authenticated(
        browser,
        settings.verification_timeout_seconds,
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
    ):
        return
    raise SessionError("Authenticated login or manual verification timed out.")


def login_to_facebook(
    browser,
    email: str,
    password: str,
    *,
    settings: BrowserSettings,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
    print_func: Callable[[str], None] = print,
    wait_factory=WebDriverWait,
) -> None:
    try:
        _login_flow(
            browser,
            email,
            password,
            settings=settings,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
            print_func=print_func,
            wait_factory=wait_factory,
        )
    except SessionError:
        raise
    except Exception as error:
        raise SessionError("Authenticated Facebook login could not be completed.") from error
```

- [ ] **Step 5: Implement restore-first session orchestration**

Append to `login.py`:

```python
CredentialsProvider = Callable[[], tuple[str, str]]
LoginFunction = Callable[..., None]


class SessionManager:
    def __init__(
        self,
        store: SessionStore,
        settings: BrowserSettings,
        credentials_provider: CredentialsProvider,
        *,
        login_func: LoginFunction = login_to_facebook,
    ) -> None:
        self._store = store
        self._settings = settings
        self._credentials_provider = credentials_provider
        self._login = login_func

    def ensure_authenticated(self, browser) -> None:
        if self._store.restore(browser):
            return
        if self._settings.headless:
            raise SessionError(
                "No valid session is available; run once interactively with --no-headless."
            )
        email, password = self._credentials_provider()
        if not email.strip() or not password:
            raise SessionError("Facebook email and password are required for interactive login.")
        self._login(
            browser,
            email.strip(),
            password,
            settings=self._settings,
        )
        self.assert_authenticated(browser)
        self._store.save(browser)

    def assert_authenticated(self, browser) -> None:
        if not is_authenticated(browser):
            raise SessionError("The authenticated Facebook session is no longer valid.")
```

- [ ] **Step 6: Run login/session and full tests**

```powershell
python -m pytest tests/unit/adapters/browser/test_login.py tests/unit/adapters/browser/test_session.py -q
python -m pytest -q
```

Expected: restore-first, headless failure, login selector reacquisition, manual success, and timeout paths pass without printing credentials or starting Firefox.

- [ ] **Step 7: Commit only login/session-manager files**

```powershell
git add -- src/fb_crawl/adapters/browser/login.py tests/unit/adapters/browser/test_login.py
git diff --cached --check
git commit -m "feat: add bounded authenticated login flow"
```

### Task 7: Bounded group-members collector

**Files:**
- Create: `src/fb_crawl/adapters/browser/members.py`
- Create: `tests/unit/adapters/browser/test_members.py`

**Interfaces:**
- Consumes: `BrowserSettings`, `wait_for_document_ready`, `SessionError`, and an injected `authenticated_func(browser) -> bool`.
- Produces: `MembersCollector.collect(browser, url, *, steps, delay_seconds) -> tuple[str, int]`.

- [ ] **Step 1: Write failing bounded-scroll tests**

```python
# tests/unit/adapters/browser/test_members.py
from fb_crawl.adapters.browser.members import MembersCollector
from fb_crawl.config import BrowserSettings


class FakeBrowser:
    page_source = "<html>members</html>"

    def __init__(self, heights: list[int]) -> None:
        self.heights = iter(heights)
        self.scrolls = 0
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)

    def execute_script(self, script: str):
        if script.startswith("return"):
            return next(self.heights)
        self.scrolls += 1
        return None


def test_members_collector_stops_when_height_stabilizes() -> None:
    browser = FakeBrowser([100, 200, 200])
    sleeps: list[float] = []
    collector = MembersCollector(
        BrowserSettings(browser_timeout_seconds=7),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        sleep_func=sleeps.append,
        jitter_func=lambda low, high: 0.25,
    )

    html, attempts = collector.collect(
        browser,
        "https://www.facebook.com/groups/1/members",
        steps=10,
        delay_seconds=2.0,
    )

    assert html == "<html>members</html>"
    assert attempts == 2
    assert browser.scrolls == 2
    assert sleeps == [2.25, 2.25]


def test_members_collector_never_exceeds_steps() -> None:
    browser = FakeBrowser([100, 200, 300, 400])
    collector = MembersCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        sleep_func=lambda seconds: None,
        jitter_func=lambda low, high: 0.0,
    )

    _, attempts = collector.collect(
        browser,
        "https://www.facebook.com/groups/1/members",
        steps=3,
        delay_seconds=0.0,
    )

    assert attempts == 3
    assert browser.scrolls == 3
```

- [ ] **Step 2: Run the focused tests and confirm the collector is missing**

```powershell
python -m pytest tests/unit/adapters/browser/test_members.py -q
```

Expected: collection fails because the members adapter does not exist.

- [ ] **Step 3: Implement navigation, validation, bounded scrolling, and early stop**

Create `src/fb_crawl/adapters/browser/members.py`:

```python
from __future__ import annotations

import random
import time
from collections.abc import Callable

from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError


class MembersCollector:
    def __init__(
        self,
        settings: BrowserSettings,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
        sleep_func: Callable[[float], None] = time.sleep,
        jitter_func: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._settings = settings
        self._authenticated = authenticated_func
        self._ready = ready_func
        self._sleep = sleep_func
        self._jitter = jitter_func

    def collect(
        self,
        browser,
        url: str,
        *,
        steps: int,
        delay_seconds: float,
    ) -> tuple[str, int]:
        try:
            browser.get(url)
            self._ready(browser, self._settings.browser_timeout_seconds)
            if not self._authenticated(browser):
                raise SessionError("The authenticated Facebook session is no longer valid.")
            previous = int(browser.execute_script("return document.body.scrollHeight"))
            attempts = 0
            for _ in range(steps):
                browser.execute_script("window.scrollTo(0, document.body.scrollHeight)")
                attempts += 1
                jitter_limit = min(delay_seconds * 0.15, 0.5)
                self._sleep(delay_seconds + self._jitter(0.0, jitter_limit))
                current = int(browser.execute_script("return document.body.scrollHeight"))
                if current <= previous:
                    break
                previous = current
            return str(browser.page_source), attempts
        except SessionError:
            raise
        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated members navigation failed.", target=url
            ) from error
```

- [ ] **Step 4: Add session-loss and failure-sanitization tests**

Append to `tests/unit/adapters/browser/test_members.py`:

```python
import pytest

from fb_crawl.core.exceptions import BrowserNavigationError, SessionError


def test_members_collector_propagates_session_loss() -> None:
    collector = MembersCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: False,
        ready_func=lambda browser, timeout: None,
    )
    with pytest.raises(SessionError):
        collector.collect(
            FakeBrowser([100]),
            "https://www.facebook.com/groups/1/members",
            steps=1,
            delay_seconds=0,
        )


def test_members_collector_sanitizes_driver_failure() -> None:
    browser = FakeBrowser([100])
    browser.get = lambda url: (_ for _ in ()).throw(RuntimeError("private HTML"))
    collector = MembersCollector(BrowserSettings())

    with pytest.raises(BrowserNavigationError) as captured:
        collector.collect(browser, "https://www.facebook.com/groups/1/members", steps=1, delay_seconds=0)

    assert "private HTML" not in captured.value.safe_message
```

Run:

```powershell
python -m pytest tests/unit/adapters/browser/test_members.py -q
python -m pytest -q
```

Expected: bounded/early-stop/session/error tests and the full suite pass.

- [ ] **Step 5: Commit only the members collector**

```powershell
git add -- src/fb_crawl/adapters/browser/members.py tests/unit/adapters/browser/test_members.py
git diff --cached --check
git commit -m "feat: collect bounded authenticated members pages"
```

### Task 8: Bounded multilingual comments collector

**Files:**
- Create: `src/fb_crawl/adapters/browser/comments.py`
- Create: `tests/unit/adapters/browser/test_comments.py`

**Interfaces:**
- Consumes: `BrowserSettings`, `wait_for_document_ready`, and an authenticated browser.
- Produces: `MORE_COMMENTS_TEXTS`, `MORE_COMMENTS_XPATH`, and `CommentsCollector.collect(browser, url, *, steps, delay_seconds) -> tuple[str, int]`.

- [ ] **Step 1: Write failing phrase and one-wait-per-attempt tests**

```python
# tests/unit/adapters/browser/test_comments.py
from fb_crawl.adapters.browser.comments import CommentsCollector, MORE_COMMENTS_TEXTS
from fb_crawl.config import BrowserSettings


class Candidate:
    def __init__(self) -> None:
        self.clicks = 0

    def is_displayed(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def click(self) -> None:
        self.clicks += 1


class Browser:
    page_source = "<html>comments</html>"
    current_url = "https://www.facebook.com/example/posts/1"

    def __init__(self, candidates: list[list[Candidate]]) -> None:
        self.candidates = iter(candidates)
        self.scrolls = 0

    def get(self, url: str) -> None:
        self.current_url = url

    def get_cookies(self):
        return [{"name": "c_user", "value": "100"}]

    def execute_script(self, script: str) -> None:
        self.scrolls += 1

    def find_elements(self, by: str, value: str):
        return next(self.candidates)


def test_phrase_list_is_valid_multilingual_unicode() -> None:
    assert "Xem thêm bình luận" in MORE_COMMENTS_TEXTS
    assert "View more comments" in MORE_COMMENTS_TEXTS
    assert "عرض مزيد من التعليقات" in MORE_COMMENTS_TEXTS
    assert "Ver más comentarios" in MORE_COMMENTS_TEXTS
    assert "Afficher plus de commentaires" in MORE_COMMENTS_TEXTS


def test_comments_collector_uses_one_wait_budget_per_attempt_and_stops_early() -> None:
    first = Candidate()
    browser = Browser([[first], []])
    waits: list[float] = []

    class Wait:
        def __init__(self, browser, timeout: float) -> None:
            waits.append(timeout)
            self.browser = browser

        def until(self, predicate):
            return predicate(self.browser)

    collector = CommentsCollector(
        BrowserSettings(browser_timeout_seconds=6),
        ready_func=lambda browser, timeout: None,
        wait_factory=Wait,
        sleep_func=lambda seconds: None,
    )
    html, attempts = collector.collect(
        browser,
        browser.current_url,
        steps=10,
        delay_seconds=0,
    )

    assert html == "<html>comments</html>"
    assert attempts == 2
    assert waits == [6, 6]
    assert first.clicks == 1
```

- [ ] **Step 2: Run the focused tests and confirm the comments adapter is missing**

```powershell
python -m pytest tests/unit/adapters/browser/test_comments.py -q
```

Expected: collection fails because the comments adapter does not exist.

- [ ] **Step 3: Implement one combined multilingual locator and bounded expansion**

Create `src/fb_crawl/adapters/browser/comments.py`:

```python
from __future__ import annotations

import time
from collections.abc import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from fb_crawl.adapters.browser.driver import wait_for_document_ready
from fb_crawl.adapters.browser.session import is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError

MORE_COMMENTS_TEXTS = (
    "Xem thêm bình luận",
    "View more comments",
    "عرض مزيد من التعليقات",
    "Ver más comentarios",
    "Afficher plus de commentaires",
)
MORE_COMMENTS_XPATH = "//*[self::button or self::div or self::span][" + " or ".join(
    f"contains(normalize-space(.), {text!r})" for text in MORE_COMMENTS_TEXTS
) + "]"


def _first_clickable(browser):
    for element in browser.find_elements(By.XPATH, MORE_COMMENTS_XPATH):
        if element.is_displayed() and element.is_enabled():
            return element
    return False


class CommentsCollector:
    def __init__(
        self,
        settings: BrowserSettings,
        *,
        authenticated_func: Callable[[object], bool] = is_authenticated,
        ready_func: Callable[[object, float], None] = wait_for_document_ready,
        wait_factory=WebDriverWait,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._authenticated = authenticated_func
        self._ready = ready_func
        self._wait_factory = wait_factory
        self._sleep = sleep_func

    def collect(
        self,
        browser,
        url: str,
        *,
        steps: int,
        delay_seconds: float,
    ) -> tuple[str, int]:
        try:
            browser.get(url)
            self._ready(browser, self._settings.browser_timeout_seconds)
            if not self._authenticated(browser):
                raise SessionError("The authenticated Facebook session is no longer valid.")
            attempts = 0
            for _ in range(steps):
                browser.execute_script("window.scrollTo(0, document.body.scrollHeight)")
                attempts += 1
                try:
                    candidate = self._wait_factory(
                        browser, self._settings.browser_timeout_seconds
                    ).until(_first_clickable)
                except TimeoutException:
                    break
                if not candidate:
                    break
                candidate.click()
                if delay_seconds:
                    self._sleep(delay_seconds)
            return str(browser.page_source), attempts
        except SessionError:
            raise
        except Exception as error:
            raise BrowserNavigationError(
                "Authenticated comments navigation failed.", target=url
            ) from error
```

- [ ] **Step 4: Add maximum-step, session-loss, and click-failure tests**

Append to `tests/unit/adapters/browser/test_comments.py`:

```python
import pytest

from fb_crawl.core.exceptions import BrowserNavigationError, SessionError


def test_comments_collector_never_exceeds_steps() -> None:
    candidates = [Candidate() for _ in range(3)]
    browser = Browser([[candidate] for candidate in candidates])

    class Wait:
        def __init__(self, browser, timeout: float) -> None:
            self.browser = browser

        def until(self, predicate):
            return predicate(self.browser)

    _, attempts = CommentsCollector(
        BrowserSettings(),
        ready_func=lambda browser, timeout: None,
        wait_factory=Wait,
        sleep_func=lambda seconds: None,
    ).collect(browser, browser.current_url, steps=3, delay_seconds=0)

    assert attempts == 3
    assert sum(candidate.clicks for candidate in candidates) == 3


def test_comments_collector_propagates_session_loss() -> None:
    browser = Browser([])
    collector = CommentsCollector(
        BrowserSettings(),
        authenticated_func=lambda browser: False,
        ready_func=lambda browser, timeout: None,
    )
    with pytest.raises(SessionError):
        collector.collect(browser, browser.current_url, steps=1, delay_seconds=0)


def test_comments_collector_sanitizes_click_failure() -> None:
    candidate = Candidate()
    candidate.click = lambda: (_ for _ in ()).throw(RuntimeError("private DOM"))
    browser = Browser([[candidate]])

    class Wait:
        def __init__(self, browser, timeout: float) -> None:
            self.browser = browser

        def until(self, predicate):
            return predicate(self.browser)

    collector = CommentsCollector(
        BrowserSettings(),
        ready_func=lambda browser, timeout: None,
        wait_factory=Wait,
    )
    with pytest.raises(BrowserNavigationError) as captured:
        collector.collect(browser, browser.current_url, steps=1, delay_seconds=0)
    assert "private DOM" not in captured.value.safe_message
```

Run:

```powershell
python -m pytest tests/unit/adapters/browser/test_comments.py -q
python -m pytest -q
```

Expected: Unicode locator, one timeout per attempt, finite click count, session propagation, and sanitized failures pass.

- [ ] **Step 5: Commit only the comments collector**

```powershell
git add -- src/fb_crawl/adapters/browser/comments.py tests/unit/adapters/browser/test_comments.py
git diff --cached --check
git commit -m "feat: expand authenticated comments with bounds"
```

### Task 9: Browser-agnostic authenticated service

**Files:**
- Create: `src/fb_crawl/services/authenticated.py`
- Modify: `src/fb_crawl/services/__init__.py`
- Create: `tests/integration/test_authenticated_service.py`

**Interfaces:**
- Consumes: `ScrapeRequest`, `AuthenticatedAction`, URL helpers, `UserRecord`, authenticated exceptions, session/member/comment/parser ports.
- Produces: `AuthenticatedService.validate(request: ScrapeRequest) -> None` and `.run(request: ScrapeRequest, browser) -> ScrapeResult[UserRecord]`.

- [ ] **Step 1: Write failing service integration tests with ports only**

```python
# tests/integration/test_authenticated_service.py
import pytest

from fb_crawl.core.exceptions import BrowserNavigationError, SessionError, ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeMode,
    ScrapeRequest,
    UserRecord,
)
from fb_crawl.services.authenticated import AuthenticatedService


class Session:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.assert_calls = 0
        self.fail_assertion = False

    def ensure_authenticated(self, browser) -> None:
        self.ensure_calls += 1

    def assert_authenticated(self, browser) -> None:
        self.assert_calls += 1
        if self.fail_assertion:
            raise SessionError("The authenticated Facebook session is no longer valid.")


class Collector:
    def __init__(self, pages: dict[str, str | Exception]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def collect(self, browser, url: str, *, steps: int, delay_seconds: float):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value, steps


class Parser:
    def parse(self, html: str, *, source: str, source_url: str):
        user_id, name = html.split(":", 1)
        return (
            UserRecord(
                user_id=user_id,
                name=name or None,
                profile_url=f"https://www.facebook.com/profile.php?id={user_id}",
                source=source,
                source_url=source_url,
            ),
        )


def request(action: AuthenticatedAction, *targets: str) -> ScrapeRequest:
    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=action,
        targets=targets,
        steps=2,
        delay_seconds=0,
    )


def test_members_service_normalizes_targets_and_counts_users() -> None:
    target = "https://www.facebook.com/groups/1/members"
    session = Session()
    members = Collector({target: "100:Member"})
    service = AuthenticatedService(session, members, Collector({}), Parser())

    result = service.run(
        request(AuthenticatedAction.MEMBERS, "https://facebook.com/groups/1"),
        object(),
    )

    assert [record.user_id for record in result.records] == ["100"]
    assert result.stats.requested == 1
    assert result.stats.discovered == 1
    assert result.stats.succeeded == 1
    assert result.stats.failed == 0
    assert session.ensure_calls == 1
    assert members.calls == [target]


def test_batch_preserves_success_when_another_target_fails() -> None:
    members_url = "https://www.facebook.com/groups/1/members"
    comments_url = "https://www.facebook.com/acme/posts/2"
    service = AuthenticatedService(
        Session(),
        Collector({members_url: "100:Member"}),
        Collector(
            {
                comments_url: BrowserNavigationError(
                    "Authenticated comments navigation failed.", target=comments_url
                )
            }
        ),
        Parser(),
    )

    result = service.run(
        request(
            AuthenticatedAction.BATCH,
            "https://facebook.com/groups/1",
            "https://facebook.com/acme/posts/2",
            "https://facebook.com/places/Nowhere/3?access_token=do-not-store",
        ),
        object(),
    )

    assert [record.user_id for record in result.records] == ["100"]
    assert [issue.code for issue in result.issues] == [
        "validation_error",
        "authenticated_navigation_failed",
    ]
    assert result.issues[0].target == "https://facebook.com/places/Nowhere/3"
    assert "access_token" not in result.issues[0].target
    assert result.stats.requested == 3
    assert result.stats.failed == 2


def test_explicit_invalid_target_fails_before_session() -> None:
    session = Session()
    service = AuthenticatedService(session, Collector({}), Collector({}), Parser())

    with pytest.raises(ValidationError, match="members target"):
        service.run(
            request(AuthenticatedAction.MEMBERS, "https://facebook.com/acme"),
            object(),
        )
    assert session.ensure_calls == 0


def test_session_loss_aborts_instead_of_becoming_target_issue() -> None:
    session = Session()
    session.fail_assertion = True
    service = AuthenticatedService(session, Collector({}), Collector({}), Parser())

    with pytest.raises(SessionError):
        service.run(
            request(AuthenticatedAction.MEMBERS, "https://facebook.com/groups/1"),
            object(),
        )
```

- [ ] **Step 2: Run the integration test and confirm the service is missing**

```powershell
python -m pytest tests/integration/test_authenticated_service.py -q
```

Expected: collection fails because `AuthenticatedService` does not exist.

- [ ] **Step 3: Define narrow ports and prepare all targets before session work**

Create `src/fb_crawl/services/authenticated.py` with imports plus protocols:

```python
from __future__ import annotations

from dataclasses import replace
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    SessionError,
    ValidationError,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.core.urls import (
    classify_authenticated_url,
    normalize_comments_url,
    normalize_members_url,
)


class SessionPort(Protocol):
    def ensure_authenticated(self, browser) -> None: ...
    def assert_authenticated(self, browser) -> None: ...


class CollectionPort(Protocol):
    def collect(
        self,
        browser,
        url: str,
        *,
        steps: int,
        delay_seconds: float,
    ) -> tuple[str, int]: ...


class UserParserPort(Protocol):
    def parse(
        self,
        html: str,
        *,
        source: str,
        source_url: str,
    ) -> tuple[UserRecord, ...]: ...


PreparedTarget = tuple[AuthenticatedAction, str]


def _safe_target(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        return urlunsplit((parsed.scheme, parsed.hostname, parsed.path, "", ""))
    return parsed.path


def _prepared_targets(
    request: ScrapeRequest,
) -> tuple[list[PreparedTarget], list[ScrapeIssue]]:
    if request.mode is not ScrapeMode.AUTHENTICATED:
        raise ValidationError("AuthenticatedService requires authenticated mode.")
    try:
        action = AuthenticatedAction(request.action)
    except ValueError as error:
        raise ValidationError("Unsupported authenticated action.") from error
    if not request.targets:
        raise ValidationError("At least one authenticated target is required.")

    prepared: list[PreparedTarget] = []
    issues: list[ScrapeIssue] = []
    for raw in request.targets:
        if action is AuthenticatedAction.MEMBERS:
            normalized = normalize_members_url(raw)
            if normalized is None:
                raise ValidationError("An unsupported members target was provided.")
            prepared.append((action, normalized))
        elif action is AuthenticatedAction.COMMENTS:
            normalized = normalize_comments_url(raw)
            if normalized is None:
                raise ValidationError("An unsupported comments target was provided.")
            prepared.append((action, normalized))
        else:
            classified = classify_authenticated_url(raw)
            if classified is None:
                issues.append(
                    ScrapeIssue(
                        code=ValidationError.code,
                        message="Unsupported authenticated batch target.",
                        target=_safe_target(raw),
                        mode=ScrapeMode.AUTHENTICATED,
                        action=AuthenticatedAction.BATCH.value,
                    )
                )
            else:
                prepared.append(classified)
    return prepared, issues
```

- [ ] **Step 4: Implement per-target isolation, run-level session failure, and merge deduplication**

Append to `authenticated.py`:

```python
def _merge_record(first: UserRecord, later: UserRecord) -> UserRecord:
    return replace(
        first,
        name=first.name or later.name,
        profile_url=first.profile_url or later.profile_url,
    )


class AuthenticatedService:
    def __init__(
        self,
        session: SessionPort,
        members: CollectionPort,
        comments: CollectionPort,
        parser: UserParserPort,
    ) -> None:
        self._session = session
        self._members = members
        self._comments = comments
        self._parser = parser

    def validate(self, request: ScrapeRequest) -> None:
        _prepared_targets(request)

    def run(
        self,
        request: ScrapeRequest,
        browser,
    ) -> ScrapeResult[UserRecord]:
        prepared, issues = _prepared_targets(request)
        if prepared:
            self._session.ensure_authenticated(browser)

        records_by_id: dict[str, UserRecord] = {}
        discovered = 0
        for action, url in prepared:
            self._session.assert_authenticated(browser)
            collector = (
                self._members
                if action is AuthenticatedAction.MEMBERS
                else self._comments
            )
            try:
                html, _ = collector.collect(
                    browser,
                    url,
                    steps=request.steps,
                    delay_seconds=request.delay_seconds,
                )
                try:
                    parsed = self._parser.parse(
                        html,
                        source=action.value,
                        source_url=url,
                    )
                except Exception as error:
                    raise BrowserParseError(
                        "Authenticated user parsing failed.", target=url
                    ) from error
                discovered += len(parsed)
                for record in parsed:
                    existing = records_by_id.get(record.user_id)
                    records_by_id[record.user_id] = (
                        record if existing is None else _merge_record(existing, record)
                    )
            except SessionError:
                raise
            except (BrowserNavigationError, BrowserParseError) as error:
                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=error.target or url,
                        mode=ScrapeMode.AUTHENTICATED,
                        action=action.value,
                    )
                )

        records = tuple(records_by_id.values())
        return ScrapeResult(
            records=records,
            issues=tuple(issues),
            stats=ScrapeStats(
                requested=len(request.targets),
                discovered=discovered,
                succeeded=len(records),
                failed=len(issues),
            ),
        )
```

Export `AuthenticatedService` from `src/fb_crawl/services/__init__.py`:

```python
from fb_crawl.services.authenticated import AuthenticatedService

__all__ = ["AuthenticatedService"]
```

- [ ] **Step 5: Add global duplicate-merge and empty-success tests**

Append to `tests/integration/test_authenticated_service.py`:

```python
def test_service_merges_duplicate_users_and_counts_raw_discovery() -> None:
    first = "https://www.facebook.com/acme/posts/1"
    second = "https://www.facebook.com/acme/posts/2"
    comments = Collector({first: "100:", second: "100:Filled Name"})
    service = AuthenticatedService(Session(), Collector({}), comments, Parser())

    result = service.run(
        request(AuthenticatedAction.COMMENTS, first, second), object()
    )

    assert len(result.records) == 1
    assert result.records[0].name == "Filled Name"
    assert result.stats.discovered == 2
    assert result.stats.succeeded == 1


class EmptyParser:
    def parse(self, html: str, *, source: str, source_url: str):
        return ()


def test_empty_parsed_target_is_success_not_failure() -> None:
    target = "https://www.facebook.com/groups/1/members"
    service = AuthenticatedService(
        Session(), Collector({target: "empty"}), Collector({}), EmptyParser()
    )

    result = service.run(request(AuthenticatedAction.MEMBERS, target), object())

    assert result.records == ()
    assert result.issues == ()
    assert result.stats.failed == 0
```

Run:

```powershell
python -m pytest tests/integration/test_authenticated_service.py -q
python -m pytest -q
```

Expected: normalization, batch isolation, session abort, dedup merge, raw discovery, and empty-success semantics pass.

- [ ] **Step 6: Commit only the authenticated service**

```powershell
git add -- src/fb_crawl/services/authenticated.py src/fb_crawl/services/__init__.py tests/integration/test_authenticated_service.py
git diff --cached --check
git commit -m "feat: orchestrate authenticated scraping"
```

### Task 10: Atomic user exporters for CSV, JSON, TXT, and XLSX

**Files:**
- Modify: `src/fb_crawl/exporters/atomic.py`
- Modify: `src/fb_crawl/exporters/json.py`
- Create: `src/fb_crawl/exporters/users.py`
- Modify: `src/fb_crawl/exporters/__init__.py`
- Create: `tests/unit/exporters/test_user_exporters.py`

**Interfaces:**
- Consumes: `ScrapeResult[UserRecord]`, existing JSON envelope, `ConfigurationError`, and `ExportError`.
- Produces: `ensure_user_format_available(format_name) -> None` and `write_users(result, path, format_name) -> bool`.

- [ ] **Step 1: Write failing CSV/JSON/TXT export tests**

```python
# tests/unit/exporters/test_user_exporters.py
import csv
import json
from pathlib import Path

import pytest

from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.exporters.users import write_users


def result() -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=(
            UserRecord(
                user_id="100",
                name="Synthetic User",
                profile_url="https://www.facebook.com/profile.php?id=100",
                source="members",
                source_url="https://www.facebook.com/groups/1/members",
            ),
            UserRecord(
                user_id="100",
                name="Duplicate",
                profile_url="https://www.facebook.com/profile.php?id=100",
                source="comments",
                source_url="https://www.facebook.com/acme/posts/1",
            ),
        ),
        issues=(
            ScrapeIssue(
                code="authenticated_navigation_failed",
                message="Authenticated comments navigation failed.",
                target="https://www.facebook.com/acme/posts/2",
                mode=ScrapeMode.AUTHENTICATED,
                action=AuthenticatedAction.COMMENTS.value,
            ),
        ),
        stats=ScrapeStats(requested=3, discovered=2, succeeded=1, failed=1),
    )


def test_user_csv_deduplicates_and_appends_issue_rows(tmp_path: Path) -> None:
    path = tmp_path / "users.csv"
    assert write_users(result(), path, "csv") is True

    with path.open(encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    assert [row["user_id"] for row in rows] == ["100", ""]
    assert rows[1]["error_code"] == "authenticated_navigation_failed"


def test_user_json_keeps_full_result_envelope(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    assert write_users(result(), path, "json") is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["records"][0]["user_id"] == "100"
    assert len(payload["records"]) == 1
    assert payload["stats"]["failed"] == 1


def test_user_txt_writes_records_and_target_issues(tmp_path: Path) -> None:
    path = tmp_path / "users.txt"
    assert write_users(result(), path, "txt") is True

    content = path.read_text(encoding="utf-8")
    assert "User ID: 100" in content
    assert "Error: [authenticated_navigation_failed]" in content
```

- [ ] **Step 2: Write failing empty-preservation and XLSX tests**

Append to `tests/unit/exporters/test_user_exporters.py`:

```python
def test_empty_user_result_preserves_existing_destination(tmp_path: Path) -> None:
    path = tmp_path / "users.csv"
    path.write_text("existing\n", encoding="utf-8")
    empty = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(requested=0, discovered=0, succeeded=0, failed=0),
    )

    assert write_users(empty, path, "csv") is False
    assert path.read_text(encoding="utf-8") == "existing\n"


def test_user_xlsx_uses_the_same_schema(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    path = tmp_path / "users.xlsx"
    assert write_users(result(), path, "xlsx") is True

    rows = list(load_workbook(path).active.values)
    assert rows[0] == (
        "user_id",
        "name",
        "profile_url",
        "source",
        "source_url",
        "error_code",
        "error_message",
    )
    assert rows[1][0] == "100"
    assert rows[2][5] == "authenticated_navigation_failed"
```

- [ ] **Step 3: Run the focused tests and confirm the user exporter is missing**

```powershell
python -m pytest tests/unit/exporters/test_user_exporters.py -q
```

Expected: collection fails because `fb_crawl.exporters.users` does not exist.

- [ ] **Step 4: Add a reusable atomic temporary-path primitive**

Append to `src/fb_crawl/exporters/atomic.py`:

```python
@contextmanager
def atomic_output_path(
    destination: Path,
    *,
    temporary_suffix: str = ".tmp",
) -> Iterator[Path]:
    destination = Path(destination)
    temporary = destination.with_name(destination.name + temporary_suffix)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        yield temporary
        with temporary.open("rb+") as file:
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    except OSError as error:
        raise ExportError(
            f"Cannot write output file {destination}.", target=str(destination)
        ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
```

Keep the existing `atomic_text_writer` behavior unchanged. Generalize only the annotation of `write_json` in `src/fb_crawl/exporters/json.py` so the existing implementation accepts every record type:

```python
RecordT = TypeVar("RecordT")


def write_json(
    result: ScrapeResult[RecordT],
    path: Path,
) -> bool:
```

Import `TypeVar`, and remove the now-unused `PageRecord` import.

- [ ] **Step 5: Implement deduplication, rows, and text formats**

Create `src/fb_crawl/exporters/users.py`:

```python
from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

from fb_crawl.core.exceptions import ConfigurationError, ExportError
from fb_crawl.core.models import ScrapeResult, UserRecord
from fb_crawl.exporters.atomic import atomic_output_path, atomic_text_writer
from fb_crawl.exporters.json import write_json

USER_FIELDS = (
    "user_id",
    "name",
    "profile_url",
    "source",
    "source_url",
    "error_code",
    "error_message",
)
USER_FORMATS = frozenset({"csv", "json", "txt", "xlsx"})


def _deduplicated_result(
    result: ScrapeResult[UserRecord],
) -> ScrapeResult[UserRecord]:
    records: dict[str, UserRecord] = {}
    for record in result.records:
        existing = records.get(record.user_id)
        if existing is None:
            records[record.user_id] = record
        else:
            records[record.user_id] = replace(
                existing,
                name=existing.name or record.name,
                profile_url=existing.profile_url or record.profile_url,
            )
    return replace(result, records=tuple(records.values()))


def _rows(result: ScrapeResult[UserRecord]) -> list[dict[str, str]]:
    rows = [
        {
            "user_id": record.user_id,
            "name": record.name or "",
            "profile_url": record.profile_url,
            "source": record.source,
            "source_url": record.source_url,
            "error_code": "",
            "error_message": "",
        }
        for record in result.records
    ]
    rows.extend(
        {
            "user_id": "",
            "name": "",
            "profile_url": "",
            "source": issue.action,
            "source_url": issue.target or "",
            "error_code": issue.code,
            "error_message": issue.message,
        }
        for issue in result.issues
    )
    return rows


def _write_csv(result: ScrapeResult[UserRecord], path: Path) -> None:
    with atomic_text_writer(path, encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=USER_FIELDS)
        writer.writeheader()
        writer.writerows(_rows(result))


def _write_txt(result: ScrapeResult[UserRecord], path: Path) -> None:
    with atomic_text_writer(path, encoding="utf-8") as file:
        for record in result.records:
            file.write(f"User ID: {record.user_id}\n")
        for issue in result.issues:
            file.write(
                f"Error: [{issue.code}] {issue.target or '-'} - {issue.message}\n"
            )
```

- [ ] **Step 6: Implement strict lazy XLSX support and format dispatch**

Append to `users.py`:

```python
def _workbook_type():
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError as error:
        raise ConfigurationError(
            'XLSX output requires: python -m pip install -e ".[xlsx]"'
        ) from error
    return Workbook


def ensure_user_format_available(format_name: str) -> None:
    if format_name not in USER_FORMATS:
        raise ConfigurationError(f"Unsupported authenticated output format: {format_name}.")
    if format_name == "xlsx":
        _workbook_type()


def _write_xlsx(result: ScrapeResult[UserRecord], path: Path) -> None:
    Workbook = _workbook_type()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "users"
    sheet.append(USER_FIELDS)
    for row in _rows(result):
        sheet.append(tuple(row[field] for field in USER_FIELDS))
    try:
        with atomic_output_path(path, temporary_suffix=".tmp.xlsx") as temporary:
            workbook.save(temporary)
    except (ConfigurationError, ExportError):
        raise
    except Exception as error:
        raise ExportError(f"Cannot write output file {path}.", target=str(path)) from error


def write_users(
    result: ScrapeResult[UserRecord],
    path: Path,
    format_name: str,
) -> bool:
    ensure_user_format_available(format_name)
    if not result.records and not result.issues:
        return False
    normalized = _deduplicated_result(result)
    destination = Path(path)
    if format_name == "csv":
        _write_csv(normalized, destination)
    elif format_name == "json":
        write_json(normalized, destination)
    elif format_name == "txt":
        _write_txt(normalized, destination)
    else:
        _write_xlsx(normalized, destination)
    return True
```

Export `write_users` and `ensure_user_format_available` from `src/fb_crawl/exporters/__init__.py`.

- [ ] **Step 7: Add no-silent-fallback and failed-write preservation tests**

Append to `tests/unit/exporters/test_user_exporters.py`:

```python
from fb_crawl.core.exceptions import ConfigurationError, ExportError


def test_xlsx_missing_dependency_does_not_change_format(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "fb_crawl.exporters.users._workbook_type",
        lambda: (_ for _ in ()).throw(ConfigurationError("XLSX extra required.")),
    )
    path = tmp_path / "users.xlsx"

    with pytest.raises(ConfigurationError, match="XLSX"):
        write_users(result(), path, "xlsx")
    assert not path.exists()
    assert not (tmp_path / "users.csv").exists()


def test_failed_xlsx_save_preserves_existing_file(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "users.xlsx"
    path.write_bytes(b"existing")

    class BrokenWorkbook:
        active = type("Sheet", (), {"title": "", "append": lambda self, row: None})()

        def save(self, temporary: Path) -> None:
            temporary.write_bytes(b"partial")
            raise OSError("disk failure")

    monkeypatch.setattr("fb_crawl.exporters.users._workbook_type", lambda: BrokenWorkbook)

    with pytest.raises(ExportError):
        write_users(result(), path, "xlsx")
    assert path.read_bytes() == b"existing"
    assert not path.with_name("users.xlsx.tmp.xlsx").exists()
```

Run:

```powershell
python -m pytest tests/unit/exporters/test_user_exporters.py tests/unit/exporters/test_csv_exporter.py tests/unit/exporters/test_json_exporter.py -q
python -m pytest -q
```

Expected: all four formats, deduplication, issue rows, empty preservation, missing-extra failure, and atomic failure preservation pass.

- [ ] **Step 8: Commit only exporter files**

```powershell
git add -- src/fb_crawl/exporters/atomic.py src/fb_crawl/exporters/json.py src/fb_crawl/exporters/users.py src/fb_crawl/exporters/__init__.py tests/unit/exporters/test_user_exporters.py
git diff --cached --check
git commit -m "feat: export authenticated user results"
```

### Task 11: Authenticated CLI parsing, lazy composition, and browser ownership

**Files:**
- Create: `src/fb_crawl/cli/authenticated.py`
- Modify: `src/fb_crawl/cli/app.py`
- Modify: `tests/unit/cli/test_public_parser.py`
- Create: `tests/unit/cli/test_authenticated_parser.py`
- Create: `tests/integration/test_authenticated_cli.py`

**Interfaces:**
- Consumes: `load_browser_settings`, `ScrapeRequest`, `AuthenticatedService`, browser adapters, and user exporters.
- Produces: `add_authenticated_parser(mode_subparsers)`, `request_from_authenticated_args(args)`, and `execute_authenticated(args) -> int`.

- [ ] **Step 1: Write failing parser and batch-input tests**

```python
# tests/unit/cli/test_authenticated_parser.py
from pathlib import Path

from fb_crawl.cli.app import build_parser
from fb_crawl.cli.authenticated import request_from_authenticated_args
from fb_crawl.core.models import AuthenticatedAction, ScrapeMode


def test_members_parser_builds_explicit_authenticated_request() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--steps",
            "7",
            "--delay",
            "1.5",
            "--headless",
        ]
    )
    request = request_from_authenticated_args(args)

    assert request.mode is ScrapeMode.AUTHENTICATED
    assert request.action is AuthenticatedAction.MEMBERS
    assert request.targets == ("https://www.facebook.com/groups/1",)
    assert request.steps == 7
    assert request.delay_seconds == 1.5
    assert args.headless is True


def test_batch_reader_ignores_blank_and_comment_lines(tmp_path: Path) -> None:
    batch = tmp_path / "targets.txt"
    batch.write_text(
        "# synthetic input\n\nhttps://www.facebook.com/groups/1\n"
        "  https://www.facebook.com/acme/posts/2  \n",
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        ["authenticated", "batch", "--input", str(batch)]
    )

    request = request_from_authenticated_args(args)

    assert request.action is AuthenticatedAction.BATCH
    assert request.targets == (
        "https://www.facebook.com/groups/1",
        "https://www.facebook.com/acme/posts/2",
    )
```

- [ ] **Step 2: Replace the public lazy-import assertion with an isolated-process test**

Once browser tests exist, pytest imports their Selenium modules during collection, so the current global `sys.modules` assertion is order-dependent. Replace `test_importing_public_cli_does_not_import_selenium` in `tests/unit/cli/test_public_parser.py` with:

```python
import subprocess
import sys


def test_building_public_cli_does_not_import_browser_extras() -> None:
    code = """
from fb_crawl.cli.app import build_parser
build_parser().parse_args(['public', 'page', 'https://www.facebook.com/example'])
import sys
assert not any(name == 'selenium' or name.startswith('selenium.') for name in sys.modules)
assert not any(name == 'bs4' or name.startswith('bs4.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
```

Remove the old top-level `import sys` if it is no longer used elsewhere.

- [ ] **Step 3: Run parser tests and confirm authenticated registration is missing**

```powershell
python -m pytest tests/unit/cli/test_authenticated_parser.py tests/unit/cli/test_public_parser.py -q
```

Expected: authenticated imports/parser tests fail while the isolated public import assertion remains meaningful.

- [ ] **Step 4: Implement the optional-dependency-free authenticated parser and batch reader**

Create the first part of `src/fb_crawl/cli/authenticated.py`; this module must not import any file under `adapters/browser` or `exporters/users` at module import time:

```python
from __future__ import annotations

import argparse
import getpass
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fb_crawl.config import BrowserSettings, load_browser_settings
from fb_crawl.core.exceptions import ConfigurationError, ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeMode,
    ScrapeRequest,
)

DEFAULT_OUTPUTS = {
    AuthenticatedAction.MEMBERS: "members",
    AuthenticatedAction.COMMENTS: "comments",
    AuthenticatedAction.BATCH: "batch",
}


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--proxy")
    parser.add_argument("--session-path", type=Path)
    parser.add_argument("--browser-timeout", type=float)
    parser.add_argument("--verification-timeout", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--format",
        choices=("csv", "json", "txt", "xlsx"),
        default="csv",
    )


def add_authenticated_parser(mode_subparsers) -> None:
    authenticated = mode_subparsers.add_parser(
        "authenticated", help="Use a validated Facebook browser session"
    )
    actions = authenticated.add_subparsers(dest="action", required=True)
    members = actions.add_parser("members", help="Collect visible group members")
    members.add_argument("urls", nargs="+")
    _common(members)
    comments = actions.add_parser("comments", help="Collect visible post commenters")
    comments.add_argument("urls", nargs="+")
    _common(comments)
    batch = actions.add_parser("batch", help="Classify and collect URLs from a file")
    batch.add_argument("--input", type=Path, required=True)
    _common(batch)


def _read_batch(path: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"Cannot read authenticated batch input {path}.") from error
    return tuple(
        stripped
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


def request_from_authenticated_args(args: argparse.Namespace) -> ScrapeRequest:
    action = AuthenticatedAction(args.action)
    targets = (
        _read_batch(args.input)
        if action is AuthenticatedAction.BATCH
        else tuple(args.urls)
    )
    if not targets:
        raise ValidationError("At least one authenticated target is required.")
    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=action,
        targets=targets,
        steps=args.steps,
        delay_seconds=args.delay,
    )
```

- [ ] **Step 5: Define a testable lazy runtime boundary**

Append to `authenticated.py`:

```python
class ServicePort(Protocol):
    def validate(self, request: ScrapeRequest) -> None: ...
    def run(self, request: ScrapeRequest, browser): ...


@dataclass(frozen=True, slots=True)
class AuthenticatedRuntime:
    create_browser: Callable[[BrowserSettings], object]
    create_service: Callable[[BrowserSettings, Callable[[], tuple[str, str]]], ServicePort]
    ensure_format: Callable[[str], None]
    write_result: Callable[[object, Path, str], bool]


def _load_runtime() -> AuthenticatedRuntime:
    try:
        from fb_crawl.adapters.browser.comments import CommentsCollector
        from fb_crawl.adapters.browser.driver import create_firefox_driver
        from fb_crawl.adapters.browser.login import SessionManager
        from fb_crawl.adapters.browser.members import MembersCollector
        from fb_crawl.adapters.browser.session import SessionStore
        from fb_crawl.adapters.browser.user_parser import UserParser
        from fb_crawl.exporters.users import (
            ensure_user_format_available,
            write_users,
        )
        from fb_crawl.services.authenticated import AuthenticatedService
    except ModuleNotFoundError as error:
        if error.name == "selenium" or str(error.name).startswith("selenium."):
            raise ConfigurationError(
                'Authenticated mode requires: python -m pip install -e ".[browser]"'
            ) from error
        if error.name == "bs4" or str(error.name).startswith("bs4."):
            raise ConfigurationError(
                'Authenticated mode requires: python -m pip install -e ".[browser]"'
            ) from error
        raise

    def create_service(settings, credentials_provider):
        return AuthenticatedService(
            SessionManager(
                SessionStore(settings.session_path),
                settings,
                credentials_provider,
            ),
            MembersCollector(settings),
            CommentsCollector(settings),
            UserParser(),
        )

    return AuthenticatedRuntime(
        create_browser=create_firefox_driver,
        create_service=create_service,
        ensure_format=ensure_user_format_available,
        write_result=write_users,
    )


def _credentials_provider() -> tuple[str, str]:
    email = input("Facebook email: ")
    password = getpass.getpass("Facebook password: ")
    return email, password
```

- [ ] **Step 6: Implement validation-before-browser, output, and unconditional cleanup**

Append to `authenticated.py`:

```python
def execute_authenticated(args: argparse.Namespace) -> int:
    request = request_from_authenticated_args(args)
    settings = load_browser_settings(
        headless=args.headless,
        proxy=args.proxy,
        session_path=args.session_path,
        browser_timeout_seconds=args.browser_timeout,
        verification_timeout_seconds=args.verification_timeout,
        repository_root=Path.cwd(),
    )
    runtime = _load_runtime()
    runtime.ensure_format(args.format)
    browser = None
    try:
        service = runtime.create_service(settings, _credentials_provider)
        service.validate(request)
        browser = runtime.create_browser(settings)
        result = service.run(request, browser)
        action = AuthenticatedAction(args.action)
        output = args.output or Path("runtime/output") / (
            f"{DEFAULT_OUTPUTS[action]}.{args.format}"
        )
        written = runtime.write_result(result, output, args.format)
        output_status = output if written else "unchanged"
        print(
            f"requested={result.stats.requested} "
            f"discovered={result.stats.discovered} "
            f"succeeded={result.stats.succeeded} "
            f"failed={result.stats.failed} "
            f"output={output_status}"
        )
        return 1 if result.has_failures else 0
    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception:
                pass
```

Import and register the authenticated parser in `src/fb_crawl/cli/app.py`:

```python
from fb_crawl.cli.authenticated import (
    add_authenticated_parser,
    execute_authenticated,
)
```

Call `add_authenticated_parser(modes)` immediately after `add_public_parser(modes)`, then add this dispatch after the public branch:

```python
if args.mode == "authenticated":
    return execute_authenticated(args)
```

- [ ] **Step 7: Write CLI integration tests for output, exit codes, and `quit`**

```python
# tests/integration/test_authenticated_cli.py
from pathlib import Path

from fb_crawl.cli.app import main
from fb_crawl.cli.authenticated import AuthenticatedRuntime
from fb_crawl.core.exceptions import SessionError, ValidationError
from fb_crawl.core.models import ScrapeResult, ScrapeStats, UserRecord


class Browser:
    def __init__(self) -> None:
        self.quit_calls = 0

    def quit(self) -> None:
        self.quit_calls += 1


class Service:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    def validate(self, request) -> None:
        return None

    def run(self, request, browser):
        if self.failure:
            raise self.failure
        return ScrapeResult(
            records=(
                UserRecord(
                    user_id="100",
                    name="Synthetic User",
                    profile_url="https://www.facebook.com/profile.php?id=100",
                    source="members",
                    source_url=request.targets[0],
                ),
            ),
            issues=(),
            stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
        )


def runtime(browser: Browser, service: Service) -> AuthenticatedRuntime:
    def write_result(result, path: Path, format_name: str) -> bool:
        path.write_text(result.records[0].user_id, encoding="utf-8")
        return True

    return AuthenticatedRuntime(
        create_browser=lambda settings: browser,
        create_service=lambda settings, credentials_provider: service,
        ensure_format=lambda format_name: None,
        write_result=write_result,
    )


def test_authenticated_command_writes_output_and_quits(tmp_path: Path, monkeypatch) -> None:
    browser = Browser()
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, Service()),
    )
    output = tmp_path / "members.csv"

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--output",
            str(output),
            "--headless",
        ]
    )

    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == "100"
    assert browser.quit_calls == 1


def test_session_failure_returns_three_and_still_quits(monkeypatch) -> None:
    browser = Browser()
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, Service(SessionError("Session unavailable."))),
    )

    exit_code = main(
        [
            "authenticated",
            "comments",
            "https://www.facebook.com/acme/posts/1",
            "--headless",
        ]
    )

    assert exit_code == 3
    assert browser.quit_calls == 1


def test_explicit_invalid_target_returns_two_before_browser_creation(monkeypatch) -> None:
    browser_creations: list[object] = []

    class InvalidService(Service):
        def validate(self, request) -> None:
            raise ValidationError("An unsupported members target was provided.")

    fake_runtime = runtime(Browser(), InvalidService())
    fake_runtime = AuthenticatedRuntime(
        create_browser=lambda settings: browser_creations.append(settings),
        create_service=fake_runtime.create_service,
        ensure_format=fake_runtime.ensure_format,
        write_result=fake_runtime.write_result,
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime", lambda: fake_runtime
    )

    assert main(["authenticated", "members", "https://facebook.com/acme"]) == 2
    assert browser_creations == []
```

- [ ] **Step 8: Test cleanup on exporter failure and lazy dependency sanitization**

Append to `tests/integration/test_authenticated_cli.py`:

```python
from fb_crawl.core.exceptions import ConfigurationError, ExportError


def test_export_failure_returns_four_and_still_quits(monkeypatch, tmp_path: Path) -> None:
    browser = Browser()
    broken = runtime(browser, Service())
    broken = AuthenticatedRuntime(
        create_browser=broken.create_browser,
        create_service=broken.create_service,
        ensure_format=broken.ensure_format,
        write_result=lambda result, path, format_name: (_ for _ in ()).throw(
            ExportError("Cannot write output file.")
        ),
    )
    monkeypatch.setattr("fb_crawl.cli.authenticated._load_runtime", lambda: broken)

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--output",
            str(tmp_path / "users.csv"),
        ]
    )

    assert exit_code == 4
    assert browser.quit_calls == 1


def test_missing_browser_extra_is_sanitized(monkeypatch) -> None:
    import builtins
    import sys

    from fb_crawl.cli import authenticated

    real_import = builtins.__import__
    for name in tuple(sys.modules):
        if (
            name == "selenium"
            or name.startswith("selenium.")
            or name.startswith("fb_crawl.adapters.browser.")
        ):
            monkeypatch.delitem(sys.modules, name, raising=False)

    def blocked_import(name, *args, **kwargs):
        if name == "selenium" or name.startswith("selenium."):
            error = ModuleNotFoundError("No module named selenium")
            error.name = "selenium"
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(ConfigurationError, match="browser"):
        authenticated._load_runtime()
```

Add `import pytest` at the top of the integration test.

Run:

```powershell
python -m pytest tests/unit/cli/test_authenticated_parser.py tests/unit/cli/test_public_parser.py tests/integration/test_authenticated_cli.py -q
python -m pytest -q
```

Expected: CLI contracts pass; public parser remains optional-dependency-free; browser `quit` runs on success, session failure, and export failure.

- [ ] **Step 9: Commit only CLI files**

```powershell
git add -- src/fb_crawl/cli/authenticated.py src/fb_crawl/cli/app.py tests/unit/cli/test_public_parser.py tests/unit/cli/test_authenticated_parser.py tests/integration/test_authenticated_cli.py
git diff --cached --check
git commit -m "feat: add authenticated CLI commands"
```

### Task 12: Operator documentation, repository safety, and final verification

**Files:**
- Create: `docs/authenticated-cli.md`
- Modify: `README.md`
- Modify: `tests/unit/test_repository_safety.py`

**Interfaces:**
- Consumes: the installed authenticated CLI and all exit/output contracts from Tasks 1–11.
- Produces: complete operator guidance and the final offline acceptance evidence; no new runtime API.

- [ ] **Step 1: Expand repository-safety assertions before writing docs**

In `tests/unit/test_repository_safety.py`, replace the ignored-path tuple with:

```python
for relative in (
    "runtime/output/pages.csv",
    "runtime/output/members.csv",
    "runtime/output/comments.json",
    "runtime/output/batch.xlsx",
    "runtime/session.json",
    "runtime/session.json.tmp",
    "runtime/geckodriver.log",
    "runtime/firefox.log",
):
```

Extend the tracked-artifact assertions with:

```python
assert "runtime/session.json" not in tracked
assert "session.json.tmp" not in tracked
assert "geckodriver.log" not in tracked
assert "firefox.log" not in tracked
```

Run:

```powershell
python -m pytest tests/unit/test_repository_safety.py -q
```

Expected: PASS because the existing `runtime/` rule covers all generated artifacts.

- [ ] **Step 2: Write the authenticated operator guide**

Create `docs/authenticated-cli.md` with this content:

````markdown
# Authenticated CLI

Authenticated mode opens Firefox and uses only data visible to a Facebook account you are authorized to operate. It does not bypass login, access controls, CAPTCHA, checkpoints, two-factor authentication, or privacy settings.

## Install

```powershell
python -m pip install -e ".[browser,dev]"
# Add XLSX only when required:
python -m pip install -e ".[browser,xlsx,dev]"
```

Firefox must be installed. Selenium Manager resolves the compatible driver.

## First interactive session

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --no-headless
```

When no valid session exists, enter the email in the terminal and the password in the hidden prompt. Complete any checkpoint or two-factor step manually in the visible browser before the verification timeout. Credentials are not persisted; validated cookies are stored at `runtime/session.json` with restricted permissions.

## Reuse the session headlessly

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --headless --steps 5 --delay 3
fb-crawl authenticated comments https://www.facebook.com/PAGE/posts/POST_ID --headless
```

Headless mode never requests credentials. It exits with code `3` when the saved session is missing, expired, or on a login/checkpoint/two-step route.

## Batch

Create a UTF-8 file with one supported URL per line. Blank lines and lines beginning with `#` are ignored.

```text
# visible group members
https://www.facebook.com/groups/GROUP_ID
# visible post comments
https://www.facebook.com/PAGE/posts/POST_ID
```

Run:

```powershell
fb-crawl authenticated batch --input runtime/targets.txt --headless --output runtime/output/batch.csv
```

Invalid targets and bounded navigation/parser failures become issue rows while other targets continue. Session loss stops the whole run.

## Options and environment

- `--steps` defaults to `5` and must be greater than zero.
- `--delay` defaults to `3.0` seconds and must be zero or greater.
- `--headless` / `--no-headless` overrides `FB_CRAWL_HEADLESS`; accepted environment values are `1`, `true`, `yes`, `on`, `0`, `false`, `no`, and `off` (case-insensitive).
- `--proxy` overrides `FB_CRAWL_PROXY`; HTTP, HTTPS, SOCKS4, and SOCKS5 URLs without embedded credentials are supported.
- `--session-path` overrides `FB_CRAWL_SESSION_PATH`; default is `runtime/session.json`.
- `--browser-timeout` overrides `FB_CRAWL_BROWSER_TIMEOUT_SECONDS`; default is `30` seconds.
- `--verification-timeout` overrides `FB_CRAWL_VERIFICATION_TIMEOUT_SECONDS`; default is `300` seconds.
- `--format` accepts `csv`, `json`, `txt`, or `xlsx`; default is `csv`.

A repository-local session path must stay under `runtime/`. An absolute external path may be used for a managed secret mount.

## Output

Default files are `runtime/output/members.csv`, `runtime/output/comments.csv`, and `runtime/output/batch.csv`. CSV/XLSX columns are:

```text
user_id,name,profile_url,source,source_url,error_code,error_message
```

JSON contains `records`, `issues`, and `stats`. TXT contains `User ID` lines followed by target-error lines. Existing output is preserved when both records and issues are empty. Every non-empty write uses a same-directory temporary file and atomic replacement.

## Exit codes

- `0`: completed without target issues.
- `1`: one or more targets failed; successful users were still exported.
- `2`: invalid target/configuration or missing optional dependency.
- `3`: session/login/manual verification unavailable.
- `4`: output could not be replaced safely.

## Security and troubleshooting

- Treat `runtime/session.json` as a bearer credential. Do not share, upload, commit, or attach it to an issue.
- Never paste passwords, cookies, full HTML, or proxy credentials into logs or bug reports.
- Missing Firefox: install Firefox, then rerun the command.
- Missing browser extra: run `python -m pip install -e ".[browser]"`.
- Missing XLSX extra: run `python -m pip install -e ".[xlsx]"`; the command will not fall back to another format.
- Invalid/expired session: rerun visibly with `--no-headless` to create a new validated session.
- Checkpoint or two-factor prompt: complete it manually in the visible browser; the tool never bypasses it.
- Empty output: increase `--steps` within a reasonable bound and confirm the account can see the requested members/comments in Firefox.
- Selector failure after a Facebook UI change: retain the safe error message and CLI version, but do not include page HTML or session data.
````

- [ ] **Step 3: Update README with authenticated install and command entry points**

Change the opening phase sentence to:

```markdown
`fb-crawl` provides explicit public HTTP and authenticated browser modes behind reusable service boundaries. Public mode never reads a browser session; authenticated mode starts only when selected explicitly.
```

Add after the public-command section:

````markdown
## Authenticated commands

Install the optional browser dependencies and bootstrap a session visibly:

```powershell
python -m pip install -e ".[browser,dev]"
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --no-headless
```

Then reuse the validated session explicitly:

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --headless
fb-crawl authenticated comments https://www.facebook.com/PAGE/posts/POST_ID --headless
fb-crawl authenticated batch --input runtime/targets.txt --headless
```

See [docs/authenticated-cli.md](docs/authenticated-cli.md) for supported URLs, session handling, formats, exit codes, and security guidance.
````

Add authenticated exit code `3` to the existing exit-code list:

```markdown
- `3`: authenticated session/login/manual verification unavailable
```

- [ ] **Step 4: Run documentation/help smoke checks**

```powershell
fb-crawl --help
fb-crawl authenticated --help
fb-crawl authenticated members --help
fb-crawl authenticated comments --help
fb-crawl authenticated batch --help
```

Expected: all commands exit `0`, list only documented flags, and do not start Firefox or prompt for credentials.

- [ ] **Step 5: Run the complete automated verification matrix**

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
python -c "from fb_crawl.cli.app import build_parser; build_parser(); import sys; assert 'selenium' not in sys.modules; assert 'bs4' not in sys.modules"
git diff --check
git status --short
```

Expected: the full new-repository suite passes, compile/pip/diff checks exit `0`, the isolated CLI import assertion is silent, and Git status contains only intentional Task 12 changes plus any pre-existing user changes.

- [ ] **Step 6: Run available legacy behavioral regression tests without the impossible `.git` gate**

```powershell
Push-Location 'D:\project\fb\Facebook-Data-Scraping-Tools'
python -B -m pytest tests --ignore=tests/test_repository_safety.py -p no:cacheprovider -q
$legacyTestExit = $LASTEXITCODE
Pop-Location
if ($legacyTestExit -ne 0) { exit $legacyTestExit }
```

Expected: behavioral source tests pass. Do not edit the source project. Its repository-safety file is excluded only because this local source copy has no `.git`; the equivalent checks are enforced in `fb-crawl`.

- [ ] **Step 7: Inspect for sensitive or generated artifacts before commit**

```powershell
git status --short
git ls-files | rg "(^|/)(runtime|\.facebook_session\.json)|session\.json|geckodriver\.log|firefox\.log|results\.(csv|json|txt|xlsx)$"
git diff --cached --check
```

Expected: the tracked-file search prints nothing. Do not open or print any real session file to inspect it.

- [ ] **Step 8: Commit documentation and safety checks**

```powershell
git add -- docs/authenticated-cli.md README.md tests/unit/test_repository_safety.py
git diff --cached --check
git commit -m "docs: document authenticated CLI operations"
```

- [ ] **Step 9: Record optional manual smoke checks without running them automatically**

With an authorized test account, the operator may separately verify:

```text
1. Visible members command creates a validated runtime/session.json.
2. A second headless members command restores that session.
3. Comments returns only users visible to the authorized account.
4. Batch exports a successful target when another target fails.
5. Firefox exits and git status contains no session, output, HTML, or browser log.
```

These checks are never run by CI or by an agent without the operator explicitly initiating the authorized login.

## Final implementation evidence

Before declaring the phase complete, record the exact outputs of:

```powershell
git log --oneline -12
python -m pytest -q
python -m compileall -q src tests
python -m pip check
git status --short
```

The phase is complete only when every automated check is green, public CLI startup remains free of browser imports, the authenticated browser is closed on every tested path, generated secrets remain untracked, and no required work in this plan remains.
