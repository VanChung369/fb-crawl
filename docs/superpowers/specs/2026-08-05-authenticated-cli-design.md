# Authenticated CLI with Selenium and Session Design

**Date:** 2026-08-05
**Status:** Approved for planning by explicit user instruction

## Context

`fb-crawl` already has an installable, offline-tested public HTTP CLI. The authenticated phase migrates the useful Selenium behaviors from `D:/project/fb/Facebook-Data-Scraping-Tools` into the same modular package without copying its script-level orchestration or any real session/output data.

The authenticated mode exists for data that the operator is authorized to access through a logged-in Facebook session. It must remain an explicit mode and must never become an automatic fallback from public scraping.

## Goals

- Add explicit `authenticated members`, `authenticated comments`, and `authenticated batch` CLI commands.
- Restore a previously validated Facebook cookie session from `runtime/session.json`.
- Bootstrap a session interactively when no valid session exists and the browser is visible.
- Stop with a focused exit code when headless mode has no valid session or Facebook requires manual verification.
- Extract typed user records from group-member and post-comment HTML.
- Bound every scroll, click, wait, and verification loop.
- Isolate failures per target in batch while treating a lost/invalid session as a run-level failure.
- Export user results atomically as CSV, JSON, TXT, or optional XLSX.
- Keep Selenium, browser startup, credentials, and session data outside public-mode imports and execution.
- Preserve the current service boundary so a future WebUI/API can call authenticated use cases without importing CLI code.

## Non-goals

- Followers or friends-list collection.
- Bypassing access controls, CAPTCHA, checkpoint, two-factor authentication, account recovery, or privacy settings.
- Persisting Facebook email or password.
- Accepting credentials through CLI arguments, environment variables, config files, logs, or batch files.
- Automatic fallback between public and authenticated modes.
- WebUI, HTTP API, database, job queue, scheduler, or distributed workers.
- Guaranteeing that Facebook selectors remain stable indefinitely.
- Copying `.facebook_session.json`, output files, browser logs, notebooks, or caches from either source project.
- Calling live Facebook or performing a real login in automated tests.

## Approaches considered

### 1. Shared service with browser/session ports — selected

Add typed core contracts, pure parsers, browser adapters, an `AuthenticatedService`, user exporters, and a thin authenticated CLI composition root. Selenium is loaded only when an authenticated command is executed.

This approach has the strongest testability and gives WebUI/API a reusable service boundary. It also keeps session and browser lifecycle explicit.

### 2. Wrap the existing Selenium scripts

Invoke `members.py`, `posts.py`, and `batch_scraper.py` from the new CLI. This is initially faster but preserves duplicated login/session orchestration, direct file writes, `SystemExit`, global browser construction, and weak service reuse.

### 3. Dynamic scraper plugin registry

Register members/comments adapters as runtime plugins. This adds lifecycle and registry complexity that is unnecessary for two authenticated actions and is deferred unless more independent scraper families appear.

## CLI contract

The installed entry point remains `fb-crawl`.

```text
fb-crawl authenticated members URL [URL ...]
fb-crawl authenticated comments URL [URL ...]
fb-crawl authenticated batch --input PATH
```

Common authenticated options:

```text
--steps N
--delay SECONDS
--headless / --no-headless
--proxy URL
--session-path PATH
--browser-timeout SECONDS
--verification-timeout SECONDS
--output PATH
--format csv|json|txt|xlsx
```

Rules:

- `--steps` defaults to `5` and must be greater than zero.
- `--delay` defaults to `3.0` seconds and must be zero or greater.
- `--headless` and `--no-headless` override the environment; otherwise `FB_CRAWL_HEADLESS` is used.
- `--proxy` overrides `FB_CRAWL_PROXY`.
- `--session-path` overrides `FB_CRAWL_SESSION_PATH`, whose default is `runtime/session.json`.
- `--browser-timeout` defaults to `30` seconds.
- `--verification-timeout` defaults to `300` seconds.
- Output format defaults to CSV.
- Default destinations are `runtime/output/members.csv`, `runtime/output/comments.csv`, and `runtime/output/batch.csv`.
- Interactive credentials are requested only after session restoration fails and only when headless mode is disabled.
- Email is read with `input`; password is read with `getpass`. Neither is stored in a model, settings object, file, environment variable, issue, or log.

A custom session path is allowed for an external secret mount. If it resolves inside the repository, it must remain under the Git-ignored `runtime/` tree; a repository-local path outside `runtime/` is rejected with `ConfigurationError`.

Batch input is UTF-8 text with one URL per line. Blank lines and lines whose first non-whitespace character is `#` are ignored. Each remaining URL is classified as a group-members or post-comments target. Invalid URLs become target-level issues and do not prevent other valid targets from running.

## Package structure

```text
src/fb_crawl/
├── config.py
├── core/
│   ├── exceptions.py
│   ├── models.py
│   └── urls.py
├── adapters/
│   └── browser/
│       ├── __init__.py
│       ├── driver.py
│       ├── session.py
│       ├── login.py
│       ├── user_parser.py
│       ├── members.py
│       └── comments.py
├── services/
│   └── authenticated.py
├── exporters/
│   └── users.py
└── cli/
    └── authenticated.py
```

Existing public modules remain unchanged except for shared core/config/JSON-export interfaces and root parser registration.

## Dependency boundary

`pyproject.toml` remains the dependency source of truth.

```toml
[project.optional-dependencies]
browser = ["selenium>=4.16,<5", "beautifulsoup4>=4.12,<5"]
xlsx = ["openpyxl>=3.1,<4"]
dev = ["pytest"]
```

Development installation for the authenticated phase:

```powershell
python -m pip install -e ".[browser,xlsx,dev]"
```

Public commands must still start when `browser` and `xlsx` extras are absent. `fb_crawl.cli.authenticated` may define argument parsers without importing Selenium or Beautiful Soup. Concrete browser/parser imports occur inside the authenticated composition function. A missing optional dependency becomes a sanitized `ConfigurationError` with install guidance, not an import traceback.

## Core contracts

### `AuthenticatedAction`

```python
class AuthenticatedAction(StrEnum):
    MEMBERS = "members"
    COMMENTS = "comments"
    BATCH = "batch"
```

### `ScrapeRequest`

The existing immutable request accepts `AuthenticatedAction` and gains:

```python
steps: int = 5
```

`steps` must be greater than zero. Browser timing, proxy, session path, credentials, output path, and output format do not belong in the service request.

### `UserRecord`

```python
@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    name: str | None
    profile_url: str
    source: str
    source_url: str
```

`user_id` may contain a numeric ID or a normalized handle when Facebook exposes no numeric ID. Deduplication uses `user_id`; the first non-empty name/profile URL is retained.

### Exceptions

- `SessionError`: code `authenticated_session_unavailable`, exit code `3`.
- `BrowserNavigationError`: code `authenticated_navigation_failed`, exit code `1`.
- `BrowserParseError`: code `authenticated_parse_failed`, exit code `1`.
- Existing `ConfigurationError`, `ValidationError`, and `ExportError` retain exit codes `2`, `2`, and `4`.

No safe exception includes a cookie value, password, raw session JSON, full HTML, proxy credentials, or sensitive query string.

## Browser configuration and lifecycle

`BrowserSettings` is an immutable config record containing:

- `headless: bool = False`
- `proxy: str | None = None`
- `session_path: Path = Path("runtime/session.json")`
- `browser_timeout_seconds: float = 30.0`
- `verification_timeout_seconds: float = 300.0`

`load_browser_settings` uses CLI override, then `FB_CRAWL_` environment variables, then defaults. Boolean environment values accept only documented true/false strings and malformed values raise `ConfigurationError`.

`driver.py` builds Firefox options with a fixed window size, disabled notifications/geolocation, and optional HTTP/SOCKS proxy preferences. Selenium Manager locates Firefox/geckodriver; no binary or driver is copied into the repository.

The CLI composition root creates exactly one browser per command and closes it in `finally`, including configuration, session, target, parser, exporter, and unexpected failures. The service receives the browser through a protocol and never constructs or owns a concrete Selenium driver.

## Session storage and validation

`SessionStore` owns cookie persistence at the configured path.

Restore flow:

1. Missing, unreadable, malformed, or non-list JSON returns `False` without exposing file content.
2. Each cookie must be a mapping with string `name` and `value`.
3. Only Selenium-compatible fields are retained: `name`, `value`, `path`, `domain`, `secure`, `httpOnly`, `expiry`, and valid `sameSite`.
4. The browser opens `https://www.facebook.com/`, adds compatible cookies, and refreshes.
5. Restoration succeeds only when a `c_user` cookie exists and the current URL is not login, checkpoint, or two-step verification.

Save flow:

1. Refuse to save until authentication passes the same validation.
2. Create the parent directory only at save time.
3. Write a same-directory temporary file using mode `0o600`.
4. Flush, `fsync`, close, and atomically replace the destination.
5. Apply owner-only permissions on platforms that support them.
6. Remove temporary files in `finally`.

Session JSON is never printed, exported, attached to an issue, or committed.

## Interactive login and manual verification

The login adapter uses stable form attributes:

- email input: `name="email"`
- password input: `name="pass"`
- submit: visible/clickable element under `form#login_form` with `role="button"`

The submit element is reacquired after filling inputs because Facebook may replace the login DOM.

After submit:

- A valid `c_user` cookie on a non-login/non-verification URL succeeds.
- A checkpoint or two-step URL in headless mode raises `SessionError` immediately.
- Interactive mode prints one manual-verification instruction and polls until success or the bounded verification timeout.
- Ordinary login polling is bounded by `browser_timeout_seconds`.
- A cookie that exists while the browser remains on a login/checkpoint/verification URL is not accepted.

`SessionManager.ensure_authenticated` first tries restore. If restore fails in headless mode, it raises `SessionError` with instructions to bootstrap interactively. If restore fails interactively, it invokes a credentials provider, logs in, validates, and saves the session.

## Authenticated URL rules

Pure core URL helpers classify targets without importing Selenium:

- Members accepts `/groups/<group-id>/members` and normalizes a plain `/groups/<group-id>` URL to its `/members` route.
- Comments accepts supported Facebook post forms: `/posts/<id>`, `/groups/<id>/posts/<id>`, `/videos/<id>`, `/reel/<id>`, `permalink.php` with `story_fbid`, and `photo.php` with `fbid`.
- Login, checkpoint, verification, generic page/profile, Places, and unrelated internal URLs are rejected for authenticated actions.
- Batch classification uses the same helpers; there is no separate substring-only classifier.

## User parsing

`user_parser.py` is a pure Beautiful Soup adapter. It receives HTML plus `source` and `source_url` and returns immutable `UserRecord` values.

Supported identity forms:

- `/groups/<group-id>/user/<id>`
- `/user/<id>`
- `profile.php?id=<id>`
- profile links with `comment_id`
- direct profile handles on anchors carrying the Facebook profile-link class used by the source behavior

The parser:

- canonicalizes relative URLs to `https://www.facebook.com`;
- strips tracking/comment query parameters from profile URLs;
- ignores Facebook internal/action paths;
- obtains a name from visible text, then `aria-label`;
- rejects action labels such as Reply, Share, Like, Trả lời, Thích, and Chia sẻ;
- deduplicates by user ID/handle while preserving first-seen order;
- never emits an empty identity.

## Members adapter

The members adapter:

1. Navigates to the normalized members URL.
2. Waits at most `browser_timeout_seconds` for document readiness.
3. Revalidates the authenticated session.
4. Reads the initial document height.
5. Scrolls at most `steps` times, sleeping the configured bounded delay plus injectable jitter.
6. Stops early when document height no longer increases.
7. Returns page source and the number of attempts.

Non-positive steps are rejected by the request model before browser work begins. Unit tests inject sleep/random functions and use fake drivers.

## Comments adapter

The comments adapter:

1. Navigates to a validated post URL and waits for readiness.
2. Revalidates the authenticated session.
3. Repeats at most `steps` times.
4. Scrolls, then searches for the first displayed/enabled “more comments” element using an ordered multilingual locator list.
5. Clicks when found and stops early when no candidate is found.
6. Uses one bounded wait budget per expansion attempt rather than one timeout per phrase.
7. Returns page source and the number of attempts.

The phrase list uses valid UTF-8 Vietnamese, English, Arabic, Spanish, and French text. Click interception/stale-element failures become target failures after bounded handling; they are not silently retried forever.

## Authenticated service

`AuthenticatedService` depends only on browser/session/member/comment/parser protocols and domain types.

Run flow:

1. Validate `ScrapeMode.AUTHENTICATED`, action, targets, and steps before session/browser navigation.
2. Ensure the session once at run start.
3. For each target, select members or comments behavior from the explicit action or batch classifier.
4. Run the bounded adapter and parse user records.
5. Deduplicate records globally by `user_id` while retaining the first non-empty fields.
6. Convert target navigation/parsing failures into `ScrapeIssue` and continue batch processing.
7. If session validation fails before or during a target, raise `SessionError` and abort the run because later results are not trustworthy.
8. Return `ScrapeResult[UserRecord]` without printing or writing files.

Authenticated stats use:

- `requested`: number of target URLs;
- `discovered`: number of user records before global deduplication;
- `succeeded`: number of unique user records returned;
- `failed`: number of target URLs that produced issues.

An empty but successfully parsed target is not a target failure.

## User exporters

`exporters/users.py` uses the existing same-directory atomic-write principle.

CSV schema:

```text
user_id,name,profile_url,source,source_url,error_code,error_message
```

Successful user rows are followed by issue rows. JSON uses the existing full `records`/`issues`/`stats` envelope. TXT writes one `User ID: <id>` line per record. XLSX uses the same columns as CSV and is available only with the `xlsx` extra.

All formats:

- preserve an existing destination when both records and issues are empty;
- deduplicate users before serialization;
- create parent directories only when writing non-empty output;
- write a temporary file in the destination directory and atomically replace the destination;
- raise `ExportError` with a safe path-only message on failure.

If XLSX is requested without `openpyxl`, raise `ConfigurationError` with install guidance. Do not silently change the requested format.

## Error and exit behavior

- `0`: run completed with no target issues.
- `1`: one or more targets failed; successful records are still exported.
- `2`: invalid input/configuration or missing optional dependency before scraping.
- `3`: authenticated session/login/checkpoint unavailable; the run stops.
- `4`: output could not be written atomically.

The root CLI maps only safe exception messages to stderr. Unexpected causes remain chained for tests/debugging but are not printed with cookie/session/credential data.

## Security and privacy boundaries

- The operator must have authorization to access and process the selected groups/posts and user data.
- The project does not bypass Facebook controls or automate manual verification.
- Passwords exist only as short-lived local variables passed directly to the login adapter.
- Session cookies are sensitive bearer credentials; the default session path and every generated output remain under Git-ignored `runtime/`.
- Cookie values, password values, raw session documents, full HTML, and proxy credentials never appear in logs, exceptions, issues, tests, fixtures, or committed files.
- Repository-safety tests cover `runtime/session.json`, its temporary file, browser logs, outputs, and legacy `.facebook_session.json`.
- Browser cleanup occurs in `finally` for every authenticated command.
- Automated fixtures contain only synthetic IDs, names, cookies, and URLs.

## Testing strategy

All automated tests are offline and use synthetic fixtures/fakes.

Unit coverage includes:

- authenticated URL normalization/classification;
- browser setting precedence and proxy parsing;
- lazy optional-dependency errors;
- session cookie compatibility, atomic permissions, round-trip restore, malformed input, and secret-safe output;
- login form locators, DOM reacquisition, headless checkpoint failure, interactive verification success/timeout, and no credential logging;
- bounded member scrolling and early stable-height stop;
- bounded comment expansion, locator priority, multilingual phrases, and one wait budget per attempt;
- user parsing, canonical profile URLs, action-label filtering, and deduplication;
- user CSV/JSON/TXT/XLSX atomic export and empty-result preservation;
- authenticated CLI parsing without importing Selenium during public CLI startup.

Integration coverage includes:

- `AuthenticatedService` members/comments/batch with fake browser/session/adapters;
- batch success preservation when another target fails;
- run abort when session becomes invalid;
- CLI-to-export flow with fake service/driver and no real browser;
- browser `quit` on success, session failure, target failure, and export failure.

Behavioral source regression tests remain passing and the source tree remains unchanged. The legacy repository-safety subtests that require a `.git` directory are not acceptance gates because this source copy has no `.git`; equivalent ignore assertions run in the new repository. A real Firefox/login test is manual-only and is not part of the default automated suite.

## Documentation and manual verification

README and `docs/authenticated-cli.md` document:

- installation of browser/XLSX extras;
- interactive first-run session bootstrap;
- headless reuse of an existing validated session;
- members/comments/batch examples;
- session/output sensitivity and backup/transfer guidance;
- exit codes and troubleshooting for missing Firefox, invalid session, checkpoint, selectors, and missing extras.

After every offline check passes, optional manual smoke checks may cover:

1. Interactive members command creates a validated `runtime/session.json`.
2. A second headless members command restores that session.
3. Comments command returns authorized visible commenters.
4. Batch preserves successful output when one target URL fails.
5. Browser exits and no cookie/password value appears in output or Git status.

Manual checks use an authorized test account and are never executed by CI.

## Acceptance criteria

- `fb-crawl authenticated --help` lists members, comments, and batch.
- Public CLI help and public tests work without importing Selenium.
- Authenticated commands use one browser, ensure a valid session, and always close the browser.
- Headless mode never prompts for credentials and returns exit code `3` without a valid session.
- Interactive mode supports bounded manual checkpoint/2FA completion but never bypasses it.
- Members/comments loops are bounded and tested with fakes.
- Batch isolates target failures but aborts on session loss.
- User records are typed, deduplicated, and exported atomically.
- CSV/JSON/TXT work with browser extra; XLSX fails clearly unless the XLSX extra is installed.
- Session/output/browser artifacts remain ignored and absent from commits/logs.
- Full offline tests, compile checks, dependency checks, CLI smoke checks, and available behavioral source-regression tests pass; the known legacy `.git`-dependent checks are replaced in the new repository.
- The two source projects remain unchanged.

## Phase boundary

This phase ends with a reusable authenticated service and CLI. Followers, WebUI, API, database, job scheduling, and deployment hardening remain separate future specs.
