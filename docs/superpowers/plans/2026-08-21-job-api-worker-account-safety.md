# Job API, Background Worker, and Account Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PostgreSQL-backed authenticated crawl queue, a separate Selenium worker with conservative account-safety controls, and a FastAPI surface for jobs, progress, account recovery, and persisted-user search.

**Architecture:** FastAPI validates and stores jobs but never opens a browser. A separate worker claims one authenticated job with `FOR UPDATE SKIP LOCKED`, runs targets sequentially through shared authenticated services, calls FBNumber, and persists each completed target directly to PostgreSQL. PostgreSQL owns job state, leases, events, account cooldown/manual-review state, and user data; local runtime files are retained only for the existing session, cache, and generated checkpoints.

**Tech Stack:** Python 3.12, dataclasses and protocols, argparse, FastAPI, Pydantic 2, Uvicorn, Selenium/Beautiful Soup optional browser extra, psycopg 3, PostgreSQL 17, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-job-api-worker-account-safety-design.md`

## Global Constraints

- PostgreSQL is the source of truth for job metadata and collected user data.
- Phase one accepts only authenticated `profile`, `members`, `comments`, `friends`, `followers`, `reactions`, and `engagement` jobs.
- The API process never imports Selenium eagerly and never creates a browser.
- One account/session named `default` and one active authenticated browser job are allowed.
- API jobs always persist; no CSV/JSON/XLSX/TXT output or caller-supplied path is accepted.
- FBNumber evidence remains `phone_1`; Facebook-visible evidence remains `phone_2`.
- One browser is reused for a job, targets run sequentially, and each target is persisted before the next begins.
- Safe mode is mandatory: navigation interval at least 8 seconds, job wall time at most 30 minutes, at least 60 minutes between authenticated jobs, steps at most 20, depth at most 2, profile limit at most 50, and transient navigation retries at most 1.
- A first Facebook rate-limit signal causes at least a 6-hour cooldown; two within 24 hours require manual review.
- Checkpoint, two-factor, CAPTCHA, restriction, unusual-activity, recovery, or repeated rate-limit signals require manual review; session expiry never triggers automatic login.
- No proxy rotation, stealth/fingerprint spoofing, CAPTCHA solving, automated credential login, or bypass of access controls.
- Events and API errors never contain credentials, cookies, tokens, session/proxy paths, raw HTML, screenshots, message content, arbitrary provider responses, or unnormalized query strings.
- API authentication uses `FB_CRAWL_API_KEY` with constant-time comparison; bind address defaults to `127.0.0.1`; CORS defaults off.
- Configuration continues to come from the process environment; library code does not automatically load `.env`.
- All regular tests are offline and synthetic; live Facebook is never used by CI.
- Do not commit, push, merge, delete runtime artifacts, or modify migrations `001_initial.sql` and `002_profile_attributes.sql`.

## File map

### PostgreSQL and domain contracts

- Create `src/fb_data_pipeline/migrations/003_job_orchestration.sql`: job, target, event, account state, constraints, and indexes.
- Create `src/fb_crawl/core/jobs.py`: enums, immutable job records, safe job options, fingerprints, state transitions, and cursor page types.
- Create `src/fb_data_pipeline/repositories/jobs.py`: job CRUD, idempotency, target/event reads, claim/lease, account state, cancel, retry, and completion transactions.
- Create `src/fb_data_pipeline/repositories/users.py`: user, phone evidence, and enrichment-attempt read queries.
- Modify `src/fb_data_pipeline/repositories/__init__.py`: export the two focused repositories.

### Browser execution and worker

- Create `src/fb_crawl/services/execution_control.py`: no-op and PostgreSQL-backed cooperative control ports plus stop exceptions.
- Create `src/fb_crawl/adapters/browser/account_safety.py`: pure URL/text/cookie safety classification.
- Modify `src/fb_crawl/adapters/browser/members.py`, `comments.py`, `relationships.py`, `reactions.py`, `profiles.py`, `profile_uid.py`, `session.py`, and `login.py`: control checks, navigation pacing, and progress callbacks around bounded operations.
- Modify `src/fb_crawl/services/authenticated.py`: service-level checks between targets, graph nodes, UID resolution, and enrichment.
- Create `src/fb_crawl/composition/__init__.py` and `src/fb_crawl/composition/authenticated.py`: shared browser/service/persistence composition.
- Modify `src/fb_crawl/cli/authenticated.py`: reuse shared composition without changing the interactive CLI contract.
- Create `src/fb_crawl/services/jobs.py`: create/cancel/retry application rules.
- Create `src/fb_crawl/services/worker.py`: lease loop, heartbeat, target execution, persistence, terminal-state derivation, and cleanup.
- Create `src/fb_crawl/cli/worker.py`: `fb-crawl worker run` entry point.

### FastAPI and operator surface

- Create `src/fb_crawl/api/__init__.py`, `config.py`, `dependencies.py`, `schemas.py`, and `app.py`.
- Create `src/fb_crawl/api/routes/__init__.py`, `health.py`, `jobs.py`, `account.py`, and `users.py`.
- Create `src/fb_crawl/cli/api.py`: `fb-crawl api serve` entry point.
- Modify `src/fb_crawl/cli/app.py`: register and dispatch API and worker modes lazily.
- Modify `pyproject.toml`: add the optional API dependencies and include them in development dependencies.
- Modify `.env.example`, `README.md`, `docs/postgresql.md`, and create `docs/job-api.md`: operator configuration, migration, processes, API, safety, and smoke test.

### Tests

- Modify `tests/unit/data_pipeline/test_migrations.py` and create `tests/integration/data_pipeline/test_job_schema.py`.
- Create `tests/unit/core/test_jobs.py`.
- Create `tests/unit/data_pipeline/test_job_repository.py` and `tests/integration/data_pipeline/test_job_repository.py`.
- Create `tests/unit/data_pipeline/test_user_query_repository.py` and `tests/integration/data_pipeline/test_user_query_repository.py`.
- Create `tests/unit/adapters/browser/test_account_safety.py`.
- Create `tests/unit/services/test_execution_control.py`, `test_jobs.py`, and `test_worker.py`.
- Modify existing browser collector/service tests for execution-control compatibility.
- Create `tests/unit/cli/test_worker_parser.py`, `test_api_parser.py`, `tests/integration/test_worker_cli.py`, and `test_api_cli.py`.
- Create `tests/unit/api/test_auth.py`, `test_health.py`, `test_job_routes.py`, `test_account_routes.py`, and `test_user_routes.py`.
- Create `tests/integration/test_job_api_worker.py` for the fake-crawler end-to-end path.
- Modify `tests/unit/test_package.py` and `tests/unit/test_repository_safety.py` for optional dependency and secret/artifact boundaries.

---

## Slice 1: Durable state, repositories, and queries

### Task 1: PostgreSQL job orchestration migration

**Files:**

- Create: `src/fb_data_pipeline/migrations/003_job_orchestration.sql`
- Modify: `tests/unit/data_pipeline/test_migrations.py`
- Create: `tests/integration/data_pipeline/test_job_schema.py`

**Interfaces:**

- Consumes: existing `MigrationRunner` and PostgreSQL 17.
- Produces: `crawl_jobs`, `crawl_targets`, `crawl_job_events`, `crawler_account_state`, required indexes, and the seeded `default` account row.

- [ ] **Step 1: Extend the packaged-migration test and confirm RED**

Change the expected versions to:

```python
assert [item.version for item in load_migrations()] == [
    "001_initial",
    "002_profile_attributes",
    "003_job_orchestration",
]
```

Assert migration 003 contains all four `CREATE TABLE` statements, the active
job partial unique index, event `(job_id, id)` index, user-search indexes, and
the seeded `default` account. Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_migrations.py -q
```

Expected: FAIL because migration 003 does not exist.

- [ ] **Step 2: Write the migration**

Use application-generated UUIDs and explicit checks. The migration must follow
this column/constraint shape:

```sql
CREATE TABLE crawl_jobs (
    id uuid PRIMARY KEY,
    mode text NOT NULL,
    action text NOT NULL,
    account_key text NOT NULL DEFAULT 'default',
    status text NOT NULL,
    request_options jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text,
    request_fingerprint text NOT NULL,
    retry_of_job_id uuid REFERENCES crawl_jobs (id) ON DELETE SET NULL,
    priority smallint NOT NULL DEFAULT 0,
    attempt integer NOT NULL DEFAULT 0,
    worker_id text,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    cancel_requested_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    requested_targets integer NOT NULL DEFAULT 0,
    completed_targets integer NOT NULL DEFAULT 0,
    failed_targets integer NOT NULL DEFAULT 0,
    discovered_users integer NOT NULL DEFAULT 0,
    persisted_users integer NOT NULL DEFAULT 0,
    provider_retries_required integer NOT NULL DEFAULT 0,
    current_target_id uuid,
    error_code text NOT NULL DEFAULT '',
    error_message text NOT NULL DEFAULT '',
    CHECK (mode = 'authenticated'),
    CHECK (action IN (
        'members', 'comments', 'profile', 'friends', 'followers',
        'reactions', 'engagement'
    )),
    CHECK (status IN (
        'queued', 'running', 'cancelling', 'succeeded', 'partial',
        'failed', 'cancelled', 'blocked'
    )),
    CHECK (
        requested_targets >= 0 AND completed_targets >= 0
        AND failed_targets >= 0 AND discovered_users >= 0
        AND persisted_users >= 0 AND provider_retries_required >= 0
    )
);
```

Create `crawl_targets` with the exact statuses and counters from the spec,
`UNIQUE(job_id, target_key)`, `position >= 0`, `attempt >= 0`, and
`ON DELETE CASCADE`. Create events with `level IN
('debug','info','warning','error')`, JSON counters, and optional target FK. Add
the `current_target_id` FK after targets exist with `ON DELETE SET NULL`.

Create `crawler_account_state` with the four allowed states and non-negative
rate-limit counter, then seed:

```sql
INSERT INTO crawler_account_state (account_key, status)
VALUES ('default', 'ready')
ON CONFLICT (account_key) DO NOTHING;
```

Indexes must include:

```sql
CREATE UNIQUE INDEX crawl_jobs_idempotency_key_idx
    ON crawl_jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX crawl_jobs_one_retry_child_idx
    ON crawl_jobs (retry_of_job_id)
    WHERE retry_of_job_id IS NOT NULL;
CREATE UNIQUE INDEX crawl_jobs_one_active_authenticated_account_idx
    ON crawl_jobs (account_key)
    WHERE mode = 'authenticated' AND status IN ('running', 'cancelling');
CREATE INDEX crawl_jobs_claim_idx
    ON crawl_jobs (priority DESC, created_at, id)
    WHERE status = 'queued';
CREATE INDEX crawl_targets_job_position_idx
    ON crawl_targets (job_id, position, id);
CREATE INDEX crawl_job_events_job_id_idx
    ON crawl_job_events (job_id, id);
CREATE INDEX facebook_users_updated_cursor_idx
    ON facebook_users (updated_at DESC, id DESC);
CREATE INDEX facebook_users_username_prefix_idx
    ON facebook_users (normalized_username text_pattern_ops);
CREATE INDEX facebook_users_display_name_prefix_idx
    ON facebook_users ((lower(display_name)) text_pattern_ops);
```

- [ ] **Step 3: Run packaged migration tests and confirm GREEN**

Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_migrations.py tests/unit/data_pipeline/test_migration_runner.py -q
```

Expected: all migration tests pass and existing migration checksums are
unchanged.

- [ ] **Step 4: Write real-schema integration tests**

Use the existing `TEST_DATABASE_URL` guard/fixture conventions. Apply all
migrations and assert:

```python
assert table_names >= {
    "crawl_jobs",
    "crawl_targets",
    "crawl_job_events",
    "crawler_account_state",
}
assert account_row == ("default", "ready")
```

Insert invalid statuses/counters and assert PostgreSQL raises
`psycopg.errors.CheckViolation`. Start one active job and assert a second active
job for `default` raises `UniqueViolation`, while multiple queued jobs succeed.

- [ ] **Step 5: Run the real schema tests**

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@127.0.0.1:5432/fb_pipeline_test"
python -m pytest tests/integration/data_pipeline/test_job_schema.py -q
```

Expected: all tests execute and pass against the dedicated `_test` database.

- [ ] **Step 6: Review checkpoint**

Run `git diff --check` and verify only migration 003 was added; migrations 001
and 002 must be byte-for-byte unchanged. Do not commit.

---

### Task 2: Typed job model, safe request policy, and cursors

**Files:**

- Create: `src/fb_crawl/core/jobs.py`
- Create: `tests/unit/core/test_jobs.py`

**Interfaces:**

- Consumes: `AuthenticatedAction` and the existing action-specific URL normalizers.
- Produces: `JobStatus`, `TargetStatus`, `AccountStatus`, `SafetyCode`, stable job exceptions, `SafeJobOptions`, `JobCreateCommand`, record dataclasses, `canonical_request_fingerprint`, `encode_cursor`, and `decode_cursor`.

- [ ] **Step 1: Write failing enum and transition tests**

Cover every allowed enum value and a transition table:

```python
@pytest.mark.parametrize(
    ("current", "next_status", "allowed"),
    [
        (JobStatus.QUEUED, JobStatus.RUNNING, True),
        (JobStatus.RUNNING, JobStatus.CANCELLING, True),
        (JobStatus.RUNNING, JobStatus.SUCCEEDED, True),
        (JobStatus.SUCCEEDED, JobStatus.RUNNING, False),
        (JobStatus.BLOCKED, JobStatus.QUEUED, False),
    ],
)
def test_job_transition_table(current, next_status, allowed) -> None:
    assert can_transition_job(current, next_status) is allowed
```

Add the matching target-state table and terminal-state predicates.

- [ ] **Step 2: Write failing option-policy tests**

Assert the default API options are exactly:

```python
options = SafeJobOptions.from_mapping(
    AuthenticatedAction.MEMBERS,
    {},
)
assert options.steps == 10
assert options.max_duration_seconds == 300
assert options.navigation_delay_seconds == 8
assert options.max_retries == 1
assert options.depth == 1
assert options.max_users == 1000
assert options.profile_limit == 20
assert options.phone_post_steps == 0
```

Parameterize rejections for `steps=21`, job target duration above 1800,
navigation delay below 8, retries above 1, depth above 2, profile limit above
50, `max_users` above 1000, phone-post steps above 20, phone-post duration above
300 seconds, unknown option names, and relationship-only options on `members`.
Assert profile fields are normalized to the existing `ProfileField` values.
`SafeJobOptions.to_scrape_request(...)` maps the safe navigation delay to both
the existing `delay_seconds` and `profile_delay_seconds`, sets the generated
checkpoint path with `resume=True`, and maps relationship/profile/phone-post
budgets without accepting `force_uid_refresh`.

- [ ] **Step 3: Write failing canonical-target and fingerprint tests**

Use one valid/invalid URL per supported action. The canonical helper must call
the existing action-specific normalizer and return:

```python
CanonicalJobTarget(
    target_key="members:https://www.facebook.com/groups/123/members",
    target_url="https://www.facebook.com/groups/123/members",
    target_kind="group_members",
)
```

Reject external hosts, `file://`, localhost/private-network hosts, credentials
in URLs, login/checkpoint paths, and action/URL mismatches. Assert reordered
JSON keys and duplicate equivalent URLs produce the same SHA-256 request
fingerprint, while any action/target/option change produces a different digest.

- [ ] **Step 4: Write failing cursor tests**

Define `KeysetCursor(sort_at: datetime, row_id: int)` and assert round-trip
through URL-safe base64 JSON. Reject invalid base64, wrong JSON shapes,
non-positive IDs, naive datetimes, and payloads larger than 512 bytes with
`ValidationError("Invalid pagination cursor.")`.

- [ ] **Step 5: Run the focused tests and confirm RED**

```powershell
python -m pytest tests/unit/core/test_jobs.py -q
```

Expected: import failure because `fb_crawl.core.jobs` does not exist.

- [ ] **Step 6: Implement immutable contracts and validation**

Define `StrEnum` values matching migration 003 and frozen, slotted dataclasses:

```python
@dataclass(frozen=True, slots=True)
class JobCreateCommand:
    action: AuthenticatedAction
    targets: tuple[CanonicalJobTarget, ...]
    options: SafeJobOptions
    account_key: str = "default"
    priority: int = 0

    def __post_init__(self) -> None:
        if not 1 <= len(self.targets) <= 100:
            raise ValidationError("A job requires 1 to 100 targets.")
        if self.account_key != "default":
            raise ValidationError("Only the default account is supported.")
```

Add frozen records for `CrawlJob`, `CrawlTarget`, `CrawlEvent`,
`CrawlerAccountState`, and generic `Page[T]`. Keep `request_options` as a typed
`SafeJobOptions` in application code and convert to/from JSON only in the
repository.

Define stable exceptions here so repositories and services share one contract:

```python
class JobNotFound(ValidationError):
    code = "job_not_found"

class JobConflict(ValidationError):
    code = "job_state_conflict"

class IdempotencyConflict(ValidationError):
    code = "job_idempotency_conflict"
```

Implement fingerprinting with sorted compact JSON:

```python
payload = json.dumps(
    command.to_canonical_dict(),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
return sha256(payload).hexdigest()
```

- [ ] **Step 7: Run focused tests and confirm GREEN**

```powershell
python -m pytest tests/unit/core/test_jobs.py tests/unit/core/test_authenticated_urls.py -q
```

Expected: all new and existing URL tests pass.

- [ ] **Step 8: Review checkpoint**

Confirm API-owned limits cannot be disabled by options and no raw argparse,
FastAPI, Selenium, or psycopg type appears in `core/jobs.py`. Do not commit.

---

### Task 3: Job creation, idempotency, event, and read repository

**Files:**

- Create: `src/fb_data_pipeline/repositories/jobs.py`
- Modify: `src/fb_data_pipeline/repositories/__init__.py`
- Create: `tests/unit/data_pipeline/test_job_repository.py`
- Create: `tests/integration/data_pipeline/test_job_repository.py`

**Interfaces:**

- Consumes: Task 2 job records and `PostgresRepository` configuration style.
- Produces: `JobRepository.create_job`, `get_job`, `list_jobs`, `list_targets`, `list_events`, `append_event`, and `request_cancel`.

- [ ] **Step 1: Write failing unit mapping and safe-error tests**

Use recording connection/cursor doubles following
`tests/unit/data_pipeline/test_postgres_repository.py`. Assert rows map to typed
records, statement timeout is set, `psycopg.Error`/`OSError` become
`DatabaseError("Database operation failed.")`, and neither database URL nor SQL
parameters enter the safe message.

- [ ] **Step 2: Write failing create/idempotency integration tests**

Create a two-target command and assert one transaction inserts one job, ordered
targets, and `job_created`. Replay the same `idempotency_key` and fingerprint
and assert `(same_job, created=False)`. Replay the key with a different
fingerprint and assert `IdempotencyConflict` with stable code
`job_idempotency_conflict` and no new rows.

Define the repository signature:

```python
def create_job(
    self,
    command: JobCreateCommand,
    *,
    idempotency_key: str,
    request_fingerprint: str,
) -> tuple[CrawlJob, bool]: ...
```

Validate idempotency keys as 1–128 printable non-whitespace characters before
connecting.

- [ ] **Step 3: Write failing read/pagination integration tests**

Seed jobs, targets, and events at deterministic timestamps. Assert job list is
ordered `(created_at DESC, id DESC)`, targets by `(position, id)`, and events by
ascending ID. Request `limit=2`, feed the returned opaque cursor into the next
call, and assert no duplicates or omissions. Event polling with `after_id`
returns only higher IDs and caps `limit` at 100.

- [ ] **Step 4: Write failing cancellation tests**

Assert a queued job and all its targets become `cancelled` atomically because no
browser owns them. A running job becomes `cancelling` for cooperative cleanup.
Both paths get one `cancel_requested` event and one timestamp. Repeating
cancellation returns the same state and does not duplicate the event. Other
terminal jobs remain unchanged. Missing IDs return `None`.

- [ ] **Step 5: Run repository tests and confirm RED**

```powershell
python -m pytest tests/unit/data_pipeline/test_job_repository.py tests/integration/data_pipeline/test_job_repository.py -q
```

Expected: import failure because `JobRepository` does not exist.

- [ ] **Step 6: Implement focused repository helpers**

Create a private `_connect()` context that sets the existing statement timeout.
Use `dict_row` or explicit column tuples consistently; do not return driver rows
outside the repository. `create_job` must use one transaction and application
UUIDs:

```python
job_id = uuid4()
target_ids = tuple(uuid4() for _ in command.targets)
```

For each fresh target, store the normalized repository-relative checkpoint path
`runtime/checkpoints/jobs/{job_id}/{target_id}.json`. Construct it from the two
application UUIDs only; never accept or interpolate a caller path.

On idempotency conflict, lock/select the existing job, compare
`request_fingerprint` with `hmac.compare_digest`, and return the existing job
only when they match. Insert only the canonical option dictionary and canonical
target URLs. `append_event` accepts a fixed `EventType`, `EventLevel`, sanitized
message, and `Mapping[str, int]`; reject other counter value types before SQL.

- [ ] **Step 7: Implement keyset reads and cancellation**

Fetch `limit + 1`, derive `next_cursor` from the last returned item, and never
use SQL `OFFSET`. The running-job conditional update is:

```sql
UPDATE crawl_jobs
SET status = 'cancelling',
    cancel_requested_at = COALESCE(cancel_requested_at, now()),
    updated_at = now()
WHERE id = %s AND status = 'running'
RETURNING ...
```

The queued path marks the job and all targets cancelled in the same transaction.
Insert the event only when a path changed the row. Keep all operations short and
transaction-scoped.

- [ ] **Step 8: Run repository tests and confirm GREEN**

```powershell
python -m pytest tests/unit/data_pipeline/test_job_repository.py tests/integration/data_pipeline/test_job_repository.py -q
```

Expected: all unit and real PostgreSQL tests pass.

- [ ] **Step 9: Review checkpoint**

Confirm repository reads are bounded, idempotency is atomic, and no browser or
provider call occurs inside a database transaction. Do not commit.

---

### Task 4: Claiming, leases, account state, retry lineage, and completion

**Files:**

- Modify: `src/fb_data_pipeline/repositories/jobs.py`
- Modify: `tests/unit/data_pipeline/test_job_repository.py`
- Modify: `tests/integration/data_pipeline/test_job_repository.py`
- Create: `src/fb_crawl/services/jobs.py`
- Create: `tests/unit/services/test_jobs.py`

**Interfaces:**

- Consumes: `JobRepository` base reads/writes from Task 3.
- Produces: claim/heartbeat/recovery/target completion primitives plus `JobService.create`, `cancel`, `retry`, `get_account`, and `acknowledge_account`.

`JobService` exposes these stable signatures:

```python
def create(
    self,
    command: JobCreateCommand,
    *,
    idempotency_key: str,
) -> tuple[CrawlJob, bool]: ...
def cancel(self, job_id: UUID) -> CrawlJob: ...
def retry(self, job_id: UUID) -> CrawlJob: ...
def get_account(self, account_key: str = "default") -> CrawlerAccountState: ...
def acknowledge_account(
    self,
    *,
    account_key: str = "default",
    acknowledged: bool,
) -> CrawlerAccountState: ...
```

- [ ] **Step 1: Write failing concurrent claim tests**

Open two real PostgreSQL connections and race two calls to:

```python
claim_next(
    worker_id: str,
    *,
    lease_duration: timedelta,
) -> ClaimedJob | None
```

Assert a queued job is claimed once, attempt increments once, worker/lease and
`job_claimed` event are stored, and the other caller gets `None`. While one job
is active, another queued default-account job remains unclaimed.

The claim transaction must lock `crawler_account_state('default')`, refresh an
expired ordinary cooldown to `ready`, reject non-ready state or an existing
active job, then select using:

```sql
SELECT id
FROM crawl_jobs
WHERE status = 'queued'
ORDER BY priority DESC, created_at, id
FOR UPDATE SKIP LOCKED
LIMIT 1
```

- [ ] **Step 2: Write failing heartbeat and stale-recovery tests**

Assert only the owning worker can extend a running job lease. A stale job with
no started target returns to queued. A stale job with a running/attempted target
becomes blocked, its target becomes blocked, the account becomes
`manual_review`, and one sanitized recovery event is written. Recovery is
idempotent.

- [ ] **Step 3: Write failing target lifecycle and completion tests**

Cover `start_target`, `update_target_progress`, `finish_target`, and
`finish_job`. Progress counters are monotonic via `GREATEST`; a non-owner cannot
write. A terminal job leaves no pending/running targets: untouched rows inherit
the reason-oriented terminal status while keeping attempt zero. Normal terminal
completion sets account cooldown to at least `finished_at + 60 minutes`.

- [ ] **Step 4: Write failing account-policy tests**

Cover:

- ordinary cooldown automatically becomes ready only after expiry;
- first Facebook rate limit records count one and at least six-hour cooldown;
- a second signal inside 24 hours sets `manual_review`;
- a signal older than 24 hours resets the count to one;
- checkpoint/CAPTCHA/restriction/unusual/recovery signals enter manual review;
- session expiry enters blocked;
- `acknowledge_account(acknowledged=False)` is rejected;
- true acknowledgement records `acknowledged_at` and yields cooldown when time
  remains, otherwise ready.

- [ ] **Step 5: Write failing retry-lineage service tests**

`JobService.retry(job_id)` must reject non-terminal jobs and non-ready account
with stable conflict exceptions. For terminal work, it creates a new job with
`retry_of_job_id`, copying canonical targets in `partial`, `failed`,
`cancelled`, or `blocked` states and excluding succeeded/skipped targets. The
old job remains unchanged. Copied targets reuse the prior server-generated
checkpoint path so a matching action, URL, and option set can resume it.
Repeating retry on the same parent returns its existing direct child; a later
retry is requested from that child after it becomes terminal.

- [ ] **Step 6: Run focused tests and confirm RED**

```powershell
python -m pytest tests/unit/services/test_jobs.py tests/unit/data_pipeline/test_job_repository.py tests/integration/data_pipeline/test_job_repository.py -q
```

Expected: failures for missing lifecycle methods and `JobService`.

- [ ] **Step 7: Implement transactional lifecycle methods**

Every mutation must use status plus worker ownership in the `WHERE` clause.
Return `False` on lost lease/ownership instead of overwriting another worker.
Store timestamps using PostgreSQL `now()` except deterministic policy methods,
which accept a supplied UTC `now` from the service. Never sleep while a
transaction is open.

Use the stable exceptions from Task 2. Keep state decisions in `JobService`; the repository performs conditional SQL
and returns typed results.

- [ ] **Step 8: Run focused tests and confirm GREEN**

```powershell
python -m pytest tests/unit/services/test_jobs.py tests/unit/data_pipeline/test_job_repository.py tests/integration/data_pipeline/test_job_repository.py -q
```

Expected: all lifecycle, concurrency, recovery, and application-service tests
pass.

- [ ] **Step 9: Review checkpoint**

Verify `FOR UPDATE SKIP LOCKED` occurs only inside the short claim transaction,
no terminal job retains pending/running targets, and retry creates a new UUID.
Do not commit.

---

### Task 5: Persisted-user search and evidence queries

**Files:**

- Create: `src/fb_data_pipeline/repositories/users.py`
- Modify: `src/fb_data_pipeline/repositories/__init__.py`
- Create: `tests/unit/data_pipeline/test_user_query_repository.py`
- Create: `tests/integration/data_pipeline/test_user_query_repository.py`

**Interfaces:**

- Consumes: `facebook_user_phone_slots`, `user_phone_evidence`, `phone_numbers`, and `enrichment_attempts`.
- Produces: `UserQuery`, `UserSummary`, `PhoneEvidenceView`, `EnrichmentAttemptView`, and bounded repository read methods.

- [ ] **Step 1: Write failing query-validation tests**

Define immutable `UserQuery(q, uid, username, phone, phone_origin, has_phone,
limit, cursor)`. Normalize username/name search with `casefold().strip()`, phone
with the existing phone normalizer, and enforce `1 <= limit <= 100`. Accept
phone origin only `fbnumber` or `fb_crawl`.

- [ ] **Step 2: Write failing real PostgreSQL search tests**

Seed users with deterministic identity, timestamps, `phone_1`, `phone_2`, and
profile fields. Cover exact UID, exact normalized phone, normalized username
prefix, lowercased display-name prefix, origin filter, `has_phone=true/false`,
combined filters, and empty result. Assert returned rows include:

```python
UserSummary(
    id=user_id,
    facebook_uid="100",
    username="sample.user",
    name="Sample User",
    profile_url="https://www.facebook.com/sample.user",
    phone_1="+84901111111",
    phone_2="+84902222222",
    address="Hanoi",
    birth_date="1990-01-02",
    gender="Male",
    created_at=created_at,
    updated_at=updated_at,
)
```

- [ ] **Step 3: Write failing keyset and detail tests**

Seed equal `updated_at` values and assert `(updated_at DESC, id DESC)` pages have
no duplicates. `get_user(id)` returns `None` when missing. Evidence is ordered
`last_captured_at DESC, id DESC`; attempts are ordered `checked_at DESC, id
DESC`; both use limit/cursor and never offset.

- [ ] **Step 4: Run focused tests and confirm RED**

```powershell
python -m pytest tests/unit/data_pipeline/test_user_query_repository.py tests/integration/data_pipeline/test_user_query_repository.py -q
```

Expected: import failure because `UserQueryRepository` does not exist.

- [ ] **Step 5: Implement parameterized keyset SQL**

Query `facebook_user_phone_slots` and use `EXISTS` for evidence filters so joins
cannot duplicate users. Build SQL only from fixed fragments selected by typed
options; every value remains a `%s` parameter. Use:

```sql
AND (slots.updated_at, slots.id) < (%s, %s)
ORDER BY slots.updated_at DESC, slots.id DESC
LIMIT %s
```

Fetch `limit + 1`, return `Page[UserSummary]`, and use the cursor helpers from
Task 2. Never return correlation/provider response content beyond stored,
documented evidence fields.

- [ ] **Step 6: Run focused tests and confirm GREEN**

```powershell
python -m pytest tests/unit/data_pipeline/test_user_query_repository.py tests/integration/data_pipeline/test_user_query_repository.py -q
```

Expected: all validation, filter, detail, and pagination tests pass.

- [ ] **Step 7: Review checkpoint**

Run `EXPLAIN` in the integration test for UID, phone, username prefix, and
updated cursor cases and assert relevant indexes appear after disabling
sequential scan within the test transaction. Do not commit.

---

## Slice 2: Cooperative browser control and worker

### Task 6: Account-safety classifier and execution-control primitives

**Files:**

- Create: `src/fb_crawl/adapters/browser/account_safety.py`
- Create: `src/fb_crawl/services/execution_control.py`
- Create: `tests/unit/adapters/browser/test_account_safety.py`
- Create: `tests/unit/services/test_execution_control.py`

**Interfaces:**

- Consumes: browser `current_url`, cookie names, and in-memory visible text.
- Produces: `SafetySignal`, `classify_account_safety`, `ExecutionControl`, `NoOpExecutionControl`, `NavigationPacer`, `NoOpNavigationPacer`, cancellation/safety guards, `CrawlCancelled`, `JobBudgetReached`, and `AccountSafetyStop`.

- [ ] **Step 1: Write failing pure classifier tests**

Parameterize canonical URLs and synthetic text for:

```text
session_expired, checkpoint, two_factor, captcha, temporary_block,
account_restricted, unusual_activity, account_recovery
```

Include English and Vietnamese marker fixtures. Assert signal priority is
restriction/checkpoint/CAPTCHA before generic rate-limit text. Normal content
with a non-empty `c_user` cookie returns `None`; login path or missing `c_user`
returns `session_expired`. Assert returned safe messages contain no supplied
page text.

- [ ] **Step 2: Write failing execution-control tests**

Assert `NoOpExecutionControl` never cancels, emits nothing, and returns no
signal. Assert cancellation-only guarding can run before session bootstrap,
while the full guard inspects account state only after authentication. Assert:

```python
with pytest.raises(CrawlCancelled):
    guard_execution(CancellingControl(), browser)

with pytest.raises(AccountSafetyStop) as captured:
    guard_execution(SignallingControl(signal), browser)
assert captured.value.signal is signal
```

Cancellation must be checked before browser inspection.

With a fake monotonic clock and sleep recorder, assert `SafeNavigationPacer(8)`
does not sleep before the first navigation, sleeps the remaining interval before
the second, and calls the cancellation-only guard between bounded sleep slices.

- [ ] **Step 3: Run tests and confirm RED**

```powershell
python -m pytest tests/unit/adapters/browser/test_account_safety.py tests/unit/services/test_execution_control.py -q
```

Expected: import failures for the two new modules.

- [ ] **Step 4: Implement pure, conservative classification**

Define:

```python
@dataclass(frozen=True, slots=True)
class SafetySignal:
    code: SafetyCode
    safe_message: str
    manual_review: bool
```

Normalize only for matching; never return the text. Read body text through one
injected `text_func(browser) -> str` capped to 100,000 characters. URL rules use
`urlparse` paths/query keys and existing Facebook host validation. Marker sets
must be literal, reviewable constants, including Vietnamese phrases such as
`"hoạt động bất thường"`, `"xác nhận đó là bạn"`, and
`"tạm thời bị chặn"`. Do not add randomized or evasion behavior.

- [ ] **Step 5: Implement narrow cooperative control**

Match the approved protocol exactly:

```python
class ExecutionControl(Protocol):
    def is_cancel_requested(self) -> bool: ...
    def emit(
        self,
        event_type: str,
        *,
        counters: Mapping[str, int] | None = None,
        safe_message: str = "",
    ) -> None: ...
    def check_account_safety(self, browser: object) -> SafetySignal | None: ...
```

`guard_execution` raises only typed internal stop exceptions. Validate event
counter keys against an allow-list and values as non-negative integers in the
PostgreSQL-backed implementation tests; raw strings are never accepted as
counters.

Add a separate navigation pacer so the approved execution-control protocol
stays narrow:

```python
class NavigationPacer(Protocol):
    def wait(self) -> None: ...
```

`NoOpNavigationPacer.wait()` returns immediately. `SafeNavigationPacer` shares
one monotonic timestamp across all job collectors, waits until at least eight
seconds since the previous top-level navigation, and checks cancellation in
bounded sleep slices. This is deterministic pacing, not randomized evasion.

- [ ] **Step 6: Run focused tests and confirm GREEN**

```powershell
python -m pytest tests/unit/adapters/browser/test_account_safety.py tests/unit/services/test_execution_control.py -q
```

Expected: all classifier and cooperative-stop tests pass.

- [ ] **Step 7: Review checkpoint**

Confirm classification never stores page text and missing authentication is a
stop signal, not an automatic-login instruction. Do not commit.

---

### Task 7: Instrument authenticated browser loops without changing CLI results

**Files:**

- Modify: `src/fb_crawl/adapters/browser/members.py`
- Modify: `src/fb_crawl/adapters/browser/comments.py`
- Modify: `src/fb_crawl/adapters/browser/relationships.py`
- Modify: `src/fb_crawl/adapters/browser/reactions.py`
- Modify: `src/fb_crawl/adapters/browser/profiles.py`
- Modify: `src/fb_crawl/adapters/browser/profile_uid.py`
- Modify: `src/fb_crawl/adapters/browser/session.py`
- Modify: `src/fb_crawl/adapters/browser/login.py`
- Modify: `src/fb_crawl/services/authenticated.py`
- Modify: corresponding files under `tests/unit/adapters/browser/`
- Modify: `tests/integration/test_authenticated_service.py`

**Interfaces:**

- Consumes: Task 6 `ExecutionControl` and `NoOpExecutionControl`.
- Produces: cooperative checks/progress around every API-supported navigation and bounded loop while keeping all existing constructor calls valid.

- [ ] **Step 1: Add failing collector cancellation/progress tests**

For each collector, inject a recording control. Assert a pre-cancelled control
causes `CrawlCancelled` before `browser.get`. During scroll/load, toggle cancel
after one progress event and assert there is no second scroll. Assert progress
uses only:

```python
control.emit("target_progress", counters={"steps_completed": attempts})
```

Existing tests that omit `control` must still behave identically.

For session restoration, assert cancellation and pacing run before the Facebook
home navigation and refresh, and account safety runs after cookie restoration.
Typed cancellation/safety stops must escape `SessionStore.restore` rather than
be converted to a simple invalid-session result. Existing interactive CLI
construction uses the no-op control and pacer.

- [ ] **Step 2: Add failing safety tests around navigation**

Return a safety signal after `browser.get`/readiness and assert
`AccountSafetyStop` is propagated rather than wrapped as
`BrowserNavigationError`. Repeat for profile directory navigation and UID
resolution. Assert no page source is parsed after the stop.

- [ ] **Step 3: Add failing service-boundary tests**

Inject control into `AuthenticatedService`; assert checks occur before session
bootstrap, each prepared target, each relationship graph node, UID resolution,
and each profile enrichment. Cancellation between two targets preserves the
first result through the checkpoint layer and does not start the second target.

- [ ] **Step 4: Run focused tests and confirm RED**

```powershell
python -m pytest tests/unit/adapters/browser tests/integration/test_authenticated_service.py -q
```

Expected: new control arguments/checks are absent.

- [ ] **Step 5: Add backward-compatible control injection**

Each affected constructor accepts:

```python
control: ExecutionControl = NOOP_EXECUTION_CONTROL
navigation_pacer: NavigationPacer = NOOP_NAVIGATION_PACER
```

Call the cancellation-only guard, then `navigation_pacer.wait()`, immediately
before each top-level `browser.get`; call the full account-safety guard after
readiness. Use the full guard before every later scroll/click/load attempt and
before capturing page source. Emit the completed attempt after it succeeds. Add
`CrawlCancelled` and `AccountSafetyStop` to explicit re-raise clauses so generic
navigation wrappers cannot hide them.

At the service level, use the same control instance and guard between logical
units. Before session bootstrap, check cancellation only because the browser has
not loaded session cookies yet. Do not add other sleeps or limits here; API
policy has already produced a bounded `ScrapeRequest`.

- [ ] **Step 6: Make checkpoint interruption cooperative**

Update `CheckpointingService` to treat `CrawlCancelled` like the existing safe
interrupt path: save current known records/issues and re-raise the typed stop so
the worker can mark `cancelled`. Treat `JobBudgetReached` the same way for
checkpoint storage while preserving its distinct type so the worker marks
`partial`. Do not convert account-safety stops into normal retryable issues. Add focused tests to
`tests/integration/test_authenticated_checkpoint.py`.

- [ ] **Step 7: Run browser/service/checkpoint tests and confirm GREEN**

```powershell
python -m pytest tests/unit/adapters/browser tests/integration/test_authenticated_service.py tests/integration/test_authenticated_checkpoint.py -q
```

Expected: all new control tests and all existing authenticated behavior pass.

- [ ] **Step 8: Review checkpoint**

Confirm all loops remain bounded, the default control is behaviorally inert,
and account/cancel stops cannot be swallowed by retry wrappers. Do not commit.

---

### Task 8: Extract shared authenticated composition

**Files:**

- Create: `src/fb_crawl/composition/__init__.py`
- Create: `src/fb_crawl/composition/authenticated.py`
- Modify: `src/fb_crawl/cli/authenticated.py`
- Modify: `tests/integration/test_authenticated_cli.py`
- Create: `tests/unit/services/test_authenticated_composition.py`

**Interfaces:**

- Consumes: existing browser adapters, `AuthenticatedService`, `CheckpointingService`, and persistence pipeline.
- Produces: `AuthenticatedComponents`, `build_authenticated_components`, `build_authenticated_persistence`, `open_authenticated_job_session`, and a non-interactive credential provider for workers.

- [ ] **Step 1: Write failing composition tests**

Assert lazy construction wires one control into `MembersCollector`,
`CommentsCollector`, `RelationshipCollector`, `ReactionsCollector`,
`ProfileEnricher`, `ProfileUidResolver`, and `AuthenticatedService`. Assert
browser dependencies are imported only when the builder is called.

Define:

```python
@dataclass(frozen=True, slots=True)
class AuthenticatedComponents:
    create_browser: Callable[[BrowserSettings], object]
    create_service: Callable[[], CheckpointingService]
```

And:

```python
def build_authenticated_components(
    settings: BrowserSettings,
    credentials_provider: Callable[[], tuple[str, str]],
    *,
    control: ExecutionControl = NOOP_EXECUTION_CONTROL,
) -> AuthenticatedComponents: ...
```

Define a worker-facing context-managed session:

```python
class AuthenticatedJobSession(Protocol):
    def validate(self, request: ScrapeRequest) -> None: ...
    def run(self, request: ScrapeRequest) -> ScrapeResult[UserRecord]: ...
    def ingest(self, result: ScrapeResult[UserRecord]) -> IngestionReport: ...

def open_authenticated_job_session(
    browser_settings: BrowserSettings,
    pipeline_settings: PipelineSettings,
    control: ExecutionControl,
    navigation_pacer: NavigationPacer,
) -> ContextManager[AuthenticatedJobSession]: ...
```

The context creates one browser, one authenticated service, and one persistence
runtime; it closes provider and browser independently in `finally`.

- [ ] **Step 2: Write failing CLI regression tests**

Monkeypatch the new builder and assert the existing CLI still validates before
Firefox, writes compatibility output only under current rules, runs persistence
when selected, prints existing summaries, closes provider/browser independently,
and retains interactive credential prompting.

- [ ] **Step 3: Write non-interactive worker credential test**

`saved_session_only_credentials()` must raise
`SessionError("Saved Facebook session requires manual login.")` without calling
`input` or `getpass`. This provider is used only by workers.

- [ ] **Step 4: Run focused tests and confirm RED**

```powershell
python -m pytest tests/unit/services/test_authenticated_composition.py tests/integration/test_authenticated_cli.py -q
```

Expected: composition module does not exist.

- [ ] **Step 5: Move composition, not business behavior**

Move the imports/wiring currently owned by `_load_runtime` and
`_load_persistence_runtime` into the new module. Keep format/export functions
inside CLI runtime where required, but use the shared service/browser and
persistence constructors. `open_authenticated_job_session` uses
`saved_session_only_credentials` and wires one shared control/pacer into every
collector. Preserve current `ConfigurationError` messages for missing browser
dependencies.

- [ ] **Step 6: Run focused tests and confirm GREEN**

```powershell
python -m pytest tests/unit/services/test_authenticated_composition.py tests/integration/test_authenticated_cli.py tests/unit/cli/test_authenticated_parser.py -q
```

Expected: all composition and existing authenticated CLI tests pass.

- [ ] **Step 7: Review checkpoint**

Verify the worker can compose services without importing argparse or exporters,
while the API can import its application without Selenium installed. Do not
commit.

---

### Task 9: Background worker, heartbeat, persistence, and terminal policy

**Files:**

- Create: `src/fb_crawl/services/worker.py`
- Create: `tests/unit/services/test_worker.py`
- Modify: `src/fb_crawl/services/__init__.py`

**Interfaces:**

- Consumes: `JobRepository`, Task 8 `open_authenticated_job_session`, and execution control.
- Produces: `WorkerPolicy`, `JobExecutionControl`, `LeaseHeartbeat`, and `CrawlWorker.run_once() -> bool`.

The worker accepts this injectable runtime boundary:

```python
RuntimeFactory = Callable[
    [ExecutionControl, NavigationPacer],
    ContextManager[AuthenticatedJobSession],
]

class CrawlWorker:
    def __init__(
        self,
        repository: JobRepository,
        runtime_factory: RuntimeFactory,
        *,
        worker_id: str,
        policy: WorkerPolicy = WorkerPolicy(),
    ) -> None: ...
    def run_once(self) -> bool: ...
```

`JobExecutionControl` implements the approved three-method protocol and adds
worker-only `guard_deadline_and_lease()` and `guard_before_persistence()`
helpers. `request_for_target` constructs the typed one-target request;
`promote_facebook_safety_issues` raises a typed safety stop for the existing
Facebook rate-limit issue code before ingestion. A deadline stop persists only
previously completed typed target results; an in-flight DOM that has not yet
become a typed result is retained only through the normal checkpoint boundary
and is never sent to FBNumber.

- [ ] **Step 1: Write failing no-work and claim tests**

With repository/service doubles, assert `run_once()` returns `False` without
creating a browser when no job is claimable. A claimed job starts one browser,
validates the session, and processes targets in position order. Each target is
converted to a one-target `ScrapeRequest` with `resume=True` and its
server-generated `checkpoint_path`; a fresh path loads as empty and a retry path
resumes the copied checkpoint.

- [ ] **Step 2: Write failing per-target persistence tests**

Return two synthetic `ScrapeResult[UserRecord]` values. Assert the worker calls
ingestion after each target, updates target/job counters from
`IngestionReport`, and does not start target two until target one persistence
finishes. Successful completion closes ingestion/provider and browser exactly
once and enters ordinary account cooldown. A normal result with records plus
ordinary issues is persisted and marks the target partial; issues without any
records mark it failed. Any provider retry requirement marks the target/job
partial after Facebook data is persisted. Any reported database failure marks
the target failed.

- [ ] **Step 3: Write failing stop/failure matrix tests**

Parameterize:

```text
CrawlCancelled       -> target/job cancelled
AccountSafetyStop    -> target/job blocked + account policy applied
SessionError         -> blocked/session_expired
RateLimitError       -> blocked/facebook_rate_limited
BrowserNavigationError with no success -> failed
DatabaseError        -> failed
JobBudgetReached     -> partial
KeyboardInterrupt    -> cancelled with cleanup
```

Assert incomplete results are never sent to FBNumber on an account-safety stop.
Also return a normal result containing the existing
`authenticated_rate_limited` issue code and assert it is promoted to the same
Facebook circuit-breaker path before ingestion, even when no exception escaped
the checkpoint service.
When target one succeeded and target two has an ordinary failure, job status is
`partial`. Every path retains the generated checkpoint and closes resources.

- [ ] **Step 4: Write failing cancellation polling tests**

`JobExecutionControl.is_cancel_requested()` reads the job's conditional state
and returns true for `cancelling`/`cancel_requested_at`. Check cancellation
before browser construction, before each target, between collector operations,
before ingestion, and before scheduling the next target.

- [ ] **Step 5: Write failing heartbeat tests**

Use a fake clock/event and assert `LeaseHeartbeat` calls repository heartbeat
every ten seconds on a dedicated daemon thread, stops/join on every exit, and
records a lost-lease flag if ownership is rejected or the database heartbeat
fails. The next control check raises `LeaseLost`, causing safe browser closure
and no further persistence.

- [ ] **Step 6: Run worker tests and confirm RED**

```powershell
python -m pytest tests/unit/services/test_worker.py -q
```

Expected: worker types do not exist.

- [ ] **Step 7: Implement immutable mandatory policy**

Define non-overridable defaults:

```python
@dataclass(frozen=True, slots=True)
class WorkerPolicy:
    lease_seconds: int = 60
    heartbeat_seconds: int = 10
    navigation_interval_seconds: int = 8
    job_timeout_seconds: int = 1800
    normal_cooldown_seconds: int = 3600
    rate_limit_cooldown_seconds: int = 21600
```

Validate heartbeat is shorter than lease and all values are positive. API job
options may be stricter but never relax this policy.

- [ ] **Step 8: Implement orchestration with explicit cleanup**

`run_once` owns this sequence:

```python
claim = repository.claim_next(
    worker_id,
    lease_duration=timedelta(seconds=policy.lease_seconds),
)
if claim is None:
    return False
with LeaseHeartbeat(...):
    pacer = SafeNavigationPacer(policy.navigation_interval_seconds, control)
    with runtime_factory(control, pacer) as session:
        for target in claim.targets:
            control.guard_deadline_and_lease()
            repository.start_target(...)
            request = request_for_target(claim.job, target, resume=True)
            session.validate(request)
            result = session.run(request)
            promote_facebook_safety_issues(result)
            control.guard_before_persistence()
            report = session.ingest(result)
            repository.finish_target(...)
repository.finish_job(...)
return True
```

Use typed exceptions for terminal mapping. Repository writes must be short and
outside browser/provider work. If terminal-state persistence itself fails,
close browser/provider first, log locally without secrets, and let the stale
lease recovery path mark the uncertain job on restart.

- [ ] **Step 9: Run worker tests and confirm GREEN**

```powershell
python -m pytest tests/unit/services/test_worker.py tests/unit/services/test_execution_control.py -q
```

Expected: all orchestration, stop matrix, heartbeat, and cleanup tests pass.

- [ ] **Step 10: Review checkpoint**

Confirm no HTTP/provider/browser action holds a PostgreSQL transaction and no
account-warning path calls ingestion. Do not commit.

---

### Task 10: Worker CLI process

**Files:**

- Create: `src/fb_crawl/cli/worker.py`
- Modify: `src/fb_crawl/cli/app.py`
- Create: `tests/unit/cli/test_worker_parser.py`
- Create: `tests/integration/test_worker_cli.py`

**Interfaces:**

- Consumes: Task 9 `CrawlWorker`, browser/pipeline settings, and repository composition.
- Produces: `fb-crawl worker run` continuous polling process with graceful Ctrl+C.

- [ ] **Step 1: Write failing parser/dispatch tests**

Assert:

```python
args = build_parser().parse_args(["worker", "run"])
assert args.mode == "worker"
assert args.worker_command == "run"
```

Importing/building the main parser without browser extras must still succeed.

- [ ] **Step 2: Write failing loop composition tests**

Patch settings/repositories/runtime/provider and a sleep recorder. Assert startup
requires database, FBNumber, browser/session config, invokes stale recovery once,
then repeatedly calls `run_once`. `False` sleeps five seconds; `True` immediately
polls again. `KeyboardInterrupt` returns exit 130. All resources close.

- [ ] **Step 3: Run focused tests and confirm RED**

```powershell
python -m pytest tests/unit/cli/test_worker_parser.py tests/integration/test_worker_cli.py -q
```

Expected: worker parser/dispatcher does not exist.

- [ ] **Step 4: Implement lazy CLI composition**

Register only `worker run`. Keep Selenium imports inside `execute_worker`, after
configuration validation. Generate worker ID as `hostname:pid:uuid4hex` without
including usernames or paths. Use a fixed five-second empty-queue poll and no
daemonization/reloader.

- [ ] **Step 5: Run focused tests and confirm GREEN**

```powershell
python -m pytest tests/unit/cli/test_worker_parser.py tests/integration/test_worker_cli.py tests/unit/test_package.py -q
```

Expected: worker CLI and package lazy-import tests pass.

- [ ] **Step 6: Review checkpoint**

Run `python -m fb_crawl.cli.app worker run --help`; verify no option can disable
cooldown, account checks, or job bounds. Do not commit.

---

## Slice 3: FastAPI, user API, and end-to-end verification

### Task 11: API dependencies, settings, authentication, and health

**Files:**

- Modify: `pyproject.toml`
- Create: `src/fb_crawl/api/__init__.py`
- Create: `src/fb_crawl/api/config.py`
- Create: `src/fb_crawl/api/dependencies.py`
- Create: `src/fb_crawl/api/routes/__init__.py`
- Create: `src/fb_crawl/api/routes/health.py`
- Create: `src/fb_crawl/api/app.py`
- Create: `tests/unit/api/test_auth.py`
- Create: `tests/unit/api/test_config.py`
- Create: `tests/unit/api/test_health.py`

**Interfaces:**

- Consumes: `DATABASE_URL`, migration repository, and optional API extra.
- Produces: `ApiSettings`, constant-time API-key dependency, application factory, `/health/live`, and `/health/ready`.

- [ ] **Step 1: Add API dependencies and install editable extras**

Add:

```toml
api = ["fastapi>=0.115,<1", "uvicorn>=0.34,<1"]
dev = [
    "pytest",
    "build>=1,<2",
    "fastapi>=0.115,<1",
    "uvicorn>=0.34,<1",
]
```

Then run:

```powershell
python -m pip install -e ".[browser,api,dev]"
python -m pip check
```

Expected: installation and dependency check exit zero.

- [ ] **Step 2: Write failing API settings tests**

Load only from an injected mapping. Require a non-empty API key of at least 32
characters, parse comma-separated exact CORS origins, default docs disabled,
and do not read `.env`. Assert secrets never appear in `repr(settings)` by
declaring the key field `repr=False`.

- [ ] **Step 3: Write failing authentication tests**

Use `TestClient` and assert missing/wrong `X-API-Key` returns 401 with
`{"code":"api_unauthorized","message":"API authentication failed."}`. Correct
key succeeds. Monkeypatch `hmac.compare_digest` and assert it is called. User
data, jobs, account, and enabled docs/schema all use this dependency; health
remains unauthenticated.

- [ ] **Step 4: Write failing health tests**

`/health/live` returns `{"status":"ok"}` without database access. Readiness
returns 200 only when connection succeeds and migration `003_job_orchestration`
is present; missing migration/database returns 503 safe JSON. Assert neither
route imports/creates Selenium or validates Facebook session.

- [ ] **Step 5: Run tests and confirm RED**

```powershell
python -m pytest tests/unit/api/test_config.py tests/unit/api/test_auth.py tests/unit/api/test_health.py -q
```

Expected: API modules/settings do not exist.

- [ ] **Step 6: Implement settings and dependency injection**

Define:

```python
@dataclass(frozen=True, slots=True)
class ApiSettings:
    api_key: str = field(repr=False)
    cors_origins: tuple[str, ...] = ()
    docs_enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
```

`create_app(settings, job_service, job_repository, user_repository,
readiness)` accepts injected ports for tests. Install a stable exception handler
for `FbCrawlError` and a generic handler that returns code
`internal_server_error` without exception text. Add CORS middleware only when
the allow-list is non-empty.

- [ ] **Step 7: Run tests and confirm GREEN**

```powershell
python -m pytest tests/unit/api/test_config.py tests/unit/api/test_auth.py tests/unit/api/test_health.py -q
```

Expected: all API foundation tests pass.

- [ ] **Step 8: Review checkpoint**

Import `fb_crawl.api.app` in an environment without Selenium and confirm it
succeeds. Inspect error JSON for absence of configured keys/URLs. Do not commit.

---

### Task 12: Job and account API routes

**Files:**

- Create: `src/fb_crawl/api/schemas.py`
- Create: `src/fb_crawl/api/routes/jobs.py`
- Create: `src/fb_crawl/api/routes/account.py`
- Modify: `src/fb_crawl/api/app.py`
- Create: `tests/unit/api/test_job_routes.py`
- Create: `tests/unit/api/test_account_routes.py`

**Interfaces:**

- Consumes: `JobService`, typed records/pages, and API auth dependency.
- Produces: all approved `/api/v1/jobs` and `/api/v1/account/default` endpoints.

- [ ] **Step 1: Write failing create-schema tests**

Use Pydantic `ConfigDict(extra="forbid")`. Assert a valid create returns 202
and a UUID/status. Reject missing headers, public mode, unsupported actions,
empty or over-100 targets, unknown option keys, unsafe bounds, `persist`,
`output`, `session_path`, `proxy`, token/credential fields, and raw CLI args.

The accepted request model is:

```python
class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["authenticated"]
    action: SupportedJobAction
    targets: list[str] = Field(min_length=1, max_length=100)
    options: dict[str, JsonValue] = Field(default_factory=dict)
```

The route converts it to `JobCreateCommand`; domain validation remains
authoritative.

- [ ] **Step 2: Write failing idempotency and state tests**

Assert same key/body returns original job, same key/different body returns 409,
missing job returns 404, invalid state/retry/account cooldown returns 409, and
cancel is idempotent. Retry response includes the new job ID and
`retry_of_job_id`.

- [ ] **Step 3: Write failing list/detail/progress tests**

Cover:

```text
GET /api/v1/jobs?limit=20&cursor=...
GET /api/v1/jobs/{id}
GET /api/v1/jobs/{id}/targets?limit=20&cursor=...
GET /api/v1/jobs/{id}/events?after_id=123&limit=100
```

Assert cursor/limit validation, stable response fields, `next_cursor`, event
ascending order, and absence of `checkpoint_path`, worker ID, lease internals,
request secrets, and raw exception values.

- [ ] **Step 4: Write failing account route tests**

Authenticated GET returns status/cooldown/safe warning/timestamps only. POST
requires exact `{"acknowledged": true}`, records acknowledgement, and returns
the new state. False, extra fields, and unsupported account keys fail. No
response contains session/proxy information.

- [ ] **Step 5: Run route tests and confirm RED**

```powershell
python -m pytest tests/unit/api/test_job_routes.py tests/unit/api/test_account_routes.py -q
```

Expected: schemas and routes do not exist.

- [ ] **Step 6: Implement response schemas and routes**

Use response models for every endpoint; never return dataclasses/driver rows
directly. Map stable exceptions to:

```text
400 validation error
404 job/account missing
409 idempotency/state/account conflict
202 create/cancel/retry accepted
```

All routers depend on the API-key dependency. `events` accepts monotonic
`after_id >= 0`; other lists use opaque cursors. Keep router functions thin:
validate Pydantic, call service/repository, map typed response.

- [ ] **Step 7: Run route tests and confirm GREEN**

```powershell
python -m pytest tests/unit/api/test_job_routes.py tests/unit/api/test_account_routes.py tests/unit/api/test_auth.py -q
```

Expected: all job/account/auth tests pass.

- [ ] **Step 8: Review checkpoint**

Inspect OpenAPI schema and confirm no request field can provide credentials,
session path, proxy, output path, or safety override. Do not commit.

---

### Task 13: Persisted-user API routes

**Files:**

- Create: `src/fb_crawl/api/routes/users.py`
- Modify: `src/fb_crawl/api/schemas.py`
- Modify: `src/fb_crawl/api/app.py`
- Create: `tests/unit/api/test_user_routes.py`

**Interfaces:**

- Consumes: Task 5 `UserQueryRepository`.
- Produces: approved user list/detail, phone evidence, and enrichment-attempt endpoints.

- [ ] **Step 1: Write failing list/filter tests**

Assert all filters are passed as a typed `UserQuery`, default/max limits are
20/100, invalid origins/phones/cursors return 400, and page responses contain
`items` plus `next_cursor`. Verify `phone_1`, `phone_2`, address, birth date,
gender, identity, profile URL, and timestamps are serialized.

- [ ] **Step 2: Write failing detail/evidence tests**

Assert missing users return 404. Evidence/attempt pages are bounded and include
stored source/origin/provider/confidence/status/timestamps but do not expose API
tokens, request headers, or raw provider responses. All endpoints reject missing
API key.

- [ ] **Step 3: Run tests and confirm RED**

```powershell
python -m pytest tests/unit/api/test_user_routes.py -q
```

Expected: users router is absent.

- [ ] **Step 4: Implement thin authenticated routes**

Define:

```text
GET /api/v1/users
GET /api/v1/users/{user_id}
GET /api/v1/users/{user_id}/phone-evidence
GET /api/v1/users/{user_id}/enrichment-attempts
```

Construct `UserQuery` from query parameters, use response models with
`from_attributes=True`, and map repository `None` to stable `user_not_found`.
Do not add export/download endpoints.

- [ ] **Step 5: Run tests and confirm GREEN**

```powershell
python -m pytest tests/unit/api/test_user_routes.py tests/unit/data_pipeline/test_user_query_repository.py -q
```

Expected: all user route and repository tests pass.

- [ ] **Step 6: Review checkpoint**

Confirm every PII endpoint requires API key and no list query uses offset or an
unbounded limit. Do not commit.

---

### Task 14: API CLI and fake-crawler end-to-end path

**Files:**

- Create: `src/fb_crawl/cli/api.py`
- Modify: `src/fb_crawl/cli/app.py`
- Create: `tests/unit/cli/test_api_parser.py`
- Create: `tests/integration/test_api_cli.py`
- Create: `tests/integration/test_job_api_worker.py`

**Interfaces:**

- Consumes: FastAPI factory, Uvicorn, job repository/service, user repository, and worker.
- Produces: `fb-crawl api serve --host 127.0.0.1 --port 8000` and verified API-to-worker-to-database flow.

- [ ] **Step 1: Write failing API CLI parser/composition tests**

Assert defaults and overrides:

```python
args = build_parser().parse_args(["api", "serve"])
assert args.host == "127.0.0.1"
assert args.port == 8000
```

Patch Uvicorn and assert it receives the already-built application, configured
host/port, and `reload=False`. Missing API key/database returns safe exit 2/5
before server start. Building parser/help does not import Uvicorn/FastAPI.

- [ ] **Step 2: Write failing end-to-end integration test**

Against the dedicated test PostgreSQL database, create the app with real
repositories and a worker with fake browser/service/FBNumber ports. Execute:

```text
POST job -> 202 queued
worker.run_once() -> True
GET job -> succeeded
GET targets -> succeeded counters
GET events -> ordered progress/completion
GET users -> persisted identity plus phone_1/phone_2/profile fields
```

Assert no compatibility output file is created.

- [ ] **Step 3: Add end-to-end cancel/block variants**

Create a job, request cancel before claim, run worker once, and assert no browser
starts and job becomes cancelled. For a synthetic account-warning signal,
assert job/target blocked, account manual review, queued next job unclaimed, no
FBNumber call, and acknowledgement is required before a later retry.

- [ ] **Step 4: Run focused tests and confirm RED**

```powershell
python -m pytest tests/unit/cli/test_api_parser.py tests/integration/test_api_cli.py tests/integration/test_job_api_worker.py -q
```

Expected: API CLI and/or end-to-end composition is incomplete.

- [ ] **Step 5: Implement lazy API CLI composition**

Import FastAPI/Uvicorn only in `execute_api`. Load API and pipeline settings,
require database/API key, construct repositories/services, create the app, then:

```python
uvicorn.run(app, host=args.host, port=args.port, reload=False)
```

Do not start a worker inside the API process. Let Ctrl+C exit cleanly with 130.

- [ ] **Step 6: Complete test injection seams**

Keep production constructors as defaults, but allow the end-to-end test to
inject fake authenticated runtime and provider ports into `CrawlWorker` without
monkeypatching Selenium internals. Production API dependencies remain real
PostgreSQL repositories.

- [ ] **Step 7: Run focused tests and confirm GREEN**

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@127.0.0.1:5432/fb_pipeline_test"
python -m pytest tests/unit/cli/test_api_parser.py tests/integration/test_api_cli.py tests/integration/test_job_api_worker.py -q
```

Expected: all CLI and end-to-end scenarios pass.

- [ ] **Step 8: Review checkpoint**

Confirm API requests return before `worker.run_once`, worker/browser ownership
is separate, and no test connects to live Facebook. Do not commit.

---

### Task 15: Documentation, repository safety, and full verification

**Files:**

- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/postgresql.md`
- Create: `docs/job-api.md`
- Modify: `tests/unit/test_package.py`
- Modify: `tests/unit/test_repository_safety.py`

**Interfaces:**

- Consumes: completed schema, worker, and API contracts.
- Produces: reproducible operator instructions and a verified, packageable phase-one system.

- [ ] **Step 1: Document environment and process startup**

Add non-secret examples:

```dotenv
FB_CRAWL_API_KEY=replace-with-at-least-32-random-characters
FB_CRAWL_API_CORS_ORIGINS=
FB_CRAWL_API_DOCS=false
```

Document that `.env` is an example only and show PowerShell process variables.
Document:

```powershell
python -m pip install -e ".[browser,api,dev]"
fb-crawl pipeline migrate
fb-crawl api serve --host 127.0.0.1 --port 8000
fb-crawl worker run
```

Include a valid authenticated members-job `Invoke-RestMethod` request with
`X-API-Key` and `Idempotency-Key`, polling commands, cancellation, account
acknowledgement, and user search. State that API jobs create no CSV output.

- [ ] **Step 2: Document safety and recovery exactly**

List fixed limits, normal/6-hour cooldown behavior, two-signals-in-24-hours
manual review, session/checkpoint/CAPTCHA behavior, non-interactive worker login,
checkpoint location, and the difference between Facebook and FBNumber rate
limits. State explicitly that acknowledgement does not solve or verify a
Facebook warning.

- [ ] **Step 3: Extend package and repository-safety tests**

Assert wheel metadata contains the API extra and migration 003. Scan source and
tracked files for forbidden real tokens/session cookies, verify runtime remains
Git-ignored, verify events use only allow-listed counters, and assert importing
base CLI/API schema modules does not require Selenium. Do not inspect or print
the user's real `.env` or `runtime/session.json`.

- [ ] **Step 4: Run all offline tests**

```powershell
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m pytest -q
```

Expected: all offline tests pass; real PostgreSQL tests skip with an explicit
reason only when `TEST_DATABASE_URL` is unset.

- [ ] **Step 5: Run all tests with dedicated PostgreSQL**

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@127.0.0.1:5432/fb_pipeline_test"
python -m pytest -q
```

Expected: every test executes and passes against the database whose name ends
in `_test`.

- [ ] **Step 6: Run static/package verification**

```powershell
python -m compileall -q src tests
python -m pip check
python -m build
python -m fb_crawl.cli.app api serve --help
python -m fb_crawl.cli.app worker run --help
git diff --check
git status --short
```

Expected: commands exit zero, wheel/sdist build, both help pages render without
starting processes, diff check is clean, and only intended uncommitted files
appear.

- [ ] **Step 7: Perform one optional manual smoke test only after review**

With an authorized account already validated in the saved session, submit one
visible target using the minimum safe budget and keep Firefox visible. Verify
quick API response, worker events, PostgreSQL user/evidence rows, and browser
closure. Stop immediately on any Facebook warning. Do not add this live action
to CI and do not run it automatically during implementation.

- [ ] **Step 8: Final spec comparison**

Compare implementation and tests line-by-line with
`docs/superpowers/specs/2026-08-21-job-api-worker-account-safety-design.md`.
Confirm public jobs, WebUI, message content, multi-account work, distributed
workers, proxy rotation, evasion, and automatic account recovery remain outside
scope. Do not commit.

---

## Completion checklist

- [ ] Migration 003 installs four orchestration tables, constraints, indexes, and the default account row without changing prior migrations.
- [ ] Idempotent job creation distinguishes same-body replay from conflicting key reuse.
- [ ] Job/target/event/user lists are bounded and keyset-paginated.
- [ ] Claiming is atomic with `FOR UPDATE SKIP LOCKED`; only one authenticated default-account job runs.
- [ ] Heartbeats renew every ten seconds and stale active browser jobs require manual review.
- [ ] Cancellation is cooperative, checkpointed, and closes browser/provider resources.
- [ ] Every API-created job is bounded by mandatory safe policy that clients cannot weaken.
- [ ] Facebook warning/session signals stop crawling before incomplete data reaches FBNumber.
- [ ] Normal, rate-limit, repeated-rate-limit, and manual-review account transitions match the spec.
- [ ] Each completed target is enriched and persisted before the next target starts; no output artifact is created.
- [ ] API process never creates Selenium and worker never receives credentials/session/proxy paths from requests.
- [ ] Job/account/user APIs require constant-time API-key authentication and return only safe schemas.
- [ ] User API exposes `phone_1`, `phone_2`, address, birth date, gender, and bounded evidence/attempt history.
- [ ] Existing interactive CLI and persistence flows remain compatible.
- [ ] Offline, real PostgreSQL, compilation, dependency, package, and repository-safety checks pass.
- [ ] No live Facebook automation runs in tests and no commit/push/merge/runtime deletion occurs.
