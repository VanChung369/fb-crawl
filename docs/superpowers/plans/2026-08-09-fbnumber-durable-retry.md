# FBNumber Durable Retry Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PostgreSQL-backed `fb-crawl pipeline retry` command that safely retries the latest failed or rate-limited FBNumber attempts after a cooldown.

**Architecture:** PostgreSQL attempt history is queried for the latest FBNumber result per user. A service-level worker holds one non-blocking session advisory lock, selects bounded candidates, reuses `EnrichmentPipeline` and `PipelinePersistenceService`, and returns a typed report for CLI formatting and exit-code decisions. HTTP requests remain outside database transactions, and no new migration or general job queue is introduced.

**Tech Stack:** Python 3.12, dataclasses, argparse, psycopg 3, PostgreSQL 17, httpx, pytest.

## Global Constraints

- PostgreSQL remains the source of truth.
- Retry only a latest `failed` or `rate_limited` FBNumber attempt.
- Never automatically retry a latest `found` or `not_found` attempt.
- Require a stored Facebook UID or username.
- Default to `--limit 20` and `--cooldown-hours 24`.
- `--force` bypasses only cooldown filtering.
- `--dry-run` never constructs or calls FBNumber and never writes enrichment data.
- Serialize this CLI worker with one non-blocking PostgreSQL session advisory lock.
- Do not hold a database transaction or row lock during provider HTTP calls.
- Do not create a job table or modify migrations `001` and `002`.
- Preserve the existing rule: FBNumber is `phone_1`, crawler evidence is `phone_2`.
- Do not commit, push, merge, or delete runtime artifacts.

## File map

- Modify `src/fb_data_pipeline/core/models.py`: define the immutable retry candidate.
- Modify `src/fb_data_pipeline/repositories/postgres.py`: acquire the worker lock and select eligible candidates.
- Create `src/fb_data_pipeline/services/retry.py`: own cooldown, worker orchestration, and retry reporting.
- Modify `src/fb_data_pipeline/services/__init__.py`: export the new service/report.
- Modify `src/fb_crawl/cli/pipeline.py`: add parser contract, runtime composition, summary, and exit codes.
- Modify `tests/unit/data_pipeline/test_models.py`: candidate invariants and bundle conversion.
- Modify `tests/unit/data_pipeline/test_postgres_repository.py`: query and lock behavior with recording connections.
- Create `tests/unit/data_pipeline/test_retry.py`: retry service behavior.
- Modify `tests/unit/cli/test_pipeline_parser.py`: parser defaults and validation contract.
- Modify `tests/integration/test_pipeline_cli.py`: CLI composition, summaries, cleanup, and exit codes.
- Modify `tests/integration/data_pipeline/test_postgres_repository.py`: real PostgreSQL selection, locking, and persisted retry outcomes.
- Modify `README.md`, `docs/postgresql.md`, and `docs/future-data-pipeline.md`: operator usage and current capability.

---

### Task 1: Typed retry candidate

**Files:**

- Modify: `src/fb_data_pipeline/core/models.py`
- Modify: `tests/unit/data_pipeline/test_models.py`

**Interfaces:**

- Consumes: existing `FacebookIdentity`, `ProviderStatus`, and `UserBundle`.
- Produces: `RetryCandidate(user_id, identity, status, checked_at, error_code)` and `RetryCandidate.to_bundle() -> UserBundle`.

- [ ] **Step 1: Write failing model tests**

Add tests proving that a candidate preserves selection metadata and creates an
empty-evidence bundle without changing its identity:

```python
def test_retry_candidate_converts_to_empty_bundle() -> None:
    checked_at = datetime(2026, 8, 8, tzinfo=UTC)
    identity = FacebookIdentity(
        uid="100",
        username="sample.user",
        name="Sample User",
        profile_url="https://www.facebook.com/sample.user",
    )

    candidate = RetryCandidate(
        user_id=7,
        identity=identity,
        status=ProviderStatus.FAILED,
        checked_at=checked_at,
        error_code="provider_transport_error",
    )

    assert candidate.to_bundle() == UserBundle(identity=identity)
    assert candidate.user_id == 7
    assert candidate.checked_at == checked_at
```

Add a validation test requiring a positive database user ID and a retryable
status:

```python
@pytest.mark.parametrize(
    ("user_id", "status"),
    [(0, ProviderStatus.FAILED), (1, ProviderStatus.FOUND)],
)
def test_retry_candidate_rejects_invalid_selection_state(
    user_id: int,
    status: ProviderStatus,
) -> None:
    with pytest.raises(ValueError):
        RetryCandidate(
            user_id=user_id,
            identity=FacebookIdentity(uid="100"),
            status=status,
            checked_at=datetime(2026, 8, 8, tzinfo=UTC),
        )
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_models.py -q
```

Expected: collection/import failure because `RetryCandidate` does not exist.

- [ ] **Step 3: Add the immutable model**

Add to `core/models.py` after `UserBundle`:

```python
@dataclass(frozen=True, slots=True)
class RetryCandidate:
    user_id: int
    identity: FacebookIdentity
    status: ProviderStatus
    checked_at: datetime
    error_code: str = ""

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("retry candidate user_id must be positive")
        if self.status not in {
            ProviderStatus.FAILED,
            ProviderStatus.RATE_LIMITED,
        }:
            raise ValueError("retry candidate status must be retryable")
        object.__setattr__(self, "error_code", _clean(self.error_code))

    def to_bundle(self) -> UserBundle:
        return UserBundle(identity=self.identity)
```

- [ ] **Step 4: Run focused model tests and confirm GREEN**

Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_models.py -q
```

Expected: all model tests pass.

- [ ] **Step 5: Review checkpoint**

Run `git diff -- src/fb_data_pipeline/core/models.py tests/unit/data_pipeline/test_models.py` and verify this task changes only the typed candidate contract. Do not commit.

---

### Task 2: PostgreSQL candidate source and worker lock

**Files:**

- Modify: `src/fb_data_pipeline/repositories/postgres.py`
- Modify: `tests/unit/data_pipeline/test_postgres_repository.py`

**Interfaces:**

- Consumes: `RetryCandidate` from Task 1.
- Produces: `PostgresRepository.fbnumber_retry_lock() -> ContextManager[bool]` and `PostgresRepository.list_fbnumber_retry_candidates(*, eligible_before: datetime | None, limit: int) -> tuple[RetryCandidate, ...]`.

- [ ] **Step 1: Extend recording database doubles**

Teach `RecordingCursor` to return configured rows for the latest-attempt query
and a boolean for `pg_try_advisory_lock`. Add `close()` methods and an
`autocommit` field to the recording cursor/connection so the tests can assert
resource cleanup and transaction-free lock behavior.

The lock assertions must cover this stable key:

```python
FB_NUMBER_RETRY_LOCK_NAME = "fb-crawl:fbnumber-durable-retry-worker:v1"
```

- [ ] **Step 2: Write failing lock tests**

Add tests for acquired and busy locks:

```python
def test_fbnumber_retry_lock_is_nonblocking_autocommit_and_released() -> None:
    cursor = RecordingCursor(lock_acquired=True)
    connection = RecordingConnection(cursor)
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(connection),
    )

    with repository.fbnumber_retry_lock() as acquired:
        assert acquired is True
        assert connection.autocommit is True

    sql = [command for command, _params in cursor.commands]
    assert any("pg_try_advisory_lock" in command for command in sql)
    assert any("pg_advisory_unlock" in command for command in sql)
    assert connection.closed is True


def test_busy_fbnumber_retry_lock_is_not_unlocked_by_non_owner() -> None:
    cursor = RecordingCursor(lock_acquired=False)
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    with repository.fbnumber_retry_lock() as acquired:
        assert acquired is False

    assert not any(
        "pg_advisory_unlock" in sql for sql, _params in cursor.commands
    )
```

- [ ] **Step 3: Write failing candidate-query tests**

Configure a returned row and assert exact typed mapping, cutoff parameters, and
limit validation:

```python
def test_repository_maps_latest_retry_candidates() -> None:
    checked_at = datetime(2026, 8, 8, tzinfo=UTC)
    cursor = RecordingCursor(
        retry_rows=[(
            7,
            "100",
            "sample.user",
            "Sample User",
            "https://www.facebook.com/sample.user",
            "failed",
            checked_at,
            "provider_transport_error",
        )]
    )
    repository = PostgresRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    candidates = repository.list_fbnumber_retry_candidates(
        eligible_before=checked_at,
        limit=20,
    )

    assert candidates[0].identity.uid == "100"
    assert candidates[0].status is ProviderStatus.FAILED
    assert candidates[0].error_code == "provider_transport_error"
    query = next(
        item for item in cursor.commands if "DISTINCT ON" in item[0]
    )
    assert query[1] == ("fbnumber", checked_at, checked_at, 20)
```

Also assert `limit=0` raises `ValueError` before connecting and
`eligible_before=None` is passed twice for forced selection.

- [ ] **Step 4: Run repository unit tests and confirm RED**

Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: failures because the lock and candidate methods do not exist.

- [ ] **Step 5: Implement the session advisory-lock context manager**

Use `contextlib.contextmanager`. Open one connection, set
`connection.autocommit = True` before executing SQL, and call:

```sql
SELECT pg_try_advisory_lock(hashtextextended(%s, 0))
```

Yield the returned boolean. In `finally`, execute the matching
`pg_advisory_unlock` only when acquisition succeeded, then close cursor and
connection. Translate only connection/acquire/release `psycopg.Error` and
`OSError` into `DatabaseError("Database operation failed.")`; do not wrap an
exception raised by the caller inside the yielded body.

- [ ] **Step 6: Implement deterministic latest-attempt selection**

Use this query shape:

```sql
WITH latest AS (
    SELECT DISTINCT ON (attempts.facebook_user_id)
        attempts.id AS attempt_id,
        attempts.facebook_user_id,
        attempts.status,
        attempts.checked_at,
        attempts.error_code
    FROM enrichment_attempts AS attempts
    WHERE attempts.provider = %s
    ORDER BY
        attempts.facebook_user_id,
        attempts.checked_at DESC,
        attempts.id DESC
)
SELECT
    users.id,
    users.facebook_uid,
    users.facebook_username,
    users.display_name,
    users.profile_url,
    latest.status,
    latest.checked_at,
    latest.error_code
FROM latest
JOIN facebook_users AS users ON users.id = latest.facebook_user_id
WHERE latest.status IN ('failed', 'rate_limited')
  AND (
      NULLIF(btrim(users.facebook_uid), '') IS NOT NULL
      OR NULLIF(btrim(users.facebook_username), '') IS NOT NULL
  )
  AND (%s IS NULL OR latest.checked_at <= %s)
ORDER BY
    latest.checked_at ASC,
    latest.attempt_id ASC,
    users.id ASC
LIMIT %s
```

Map rows to `RetryCandidate` and use `ProviderStatus(str(row[5]))`. Configure
the existing statement timeout before the selection query. Wrap driver and OS
errors with the same safe `DatabaseError` used by writes.

- [ ] **Step 7: Run repository unit tests and confirm GREEN**

Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: all repository unit tests pass, including existing write tests.

- [ ] **Step 8: Review checkpoint**

Run `git diff --check` and inspect the repository diff. Confirm the advisory
lock is session-level, non-blocking, and held without an open transaction. Do
not commit.

---

### Task 3: Durable retry orchestration service

**Files:**

- Create: `src/fb_data_pipeline/services/retry.py`
- Create: `tests/unit/data_pipeline/test_retry.py`
- Modify: `src/fb_data_pipeline/services/__init__.py`

**Interfaces:**

- Consumes: `RetryCandidate`, `EnrichmentPipeline.run_bundles(...)`, and `PipelinePersistenceService.persist(...)`.
- Produces: `RetryReport`, `FBNumberRetryService(source, enrichment: EnrichmentPort | None = None, persistence: PersistencePort | None = None)`, and `FBNumberRetryService.run(*, limit: int = 20, cooldown: timedelta = timedelta(hours=24), force: bool = False, dry_run: bool = False) -> RetryReport`.

- [ ] **Step 1: Write repository, enrichment, and persistence test doubles**

The repository double must expose a context-managed lock and record
`eligible_before`/`limit`. The enrichment double must record bundles and return
a `PipelineRun` with configured provider statuses. The persistence double must
record the run and return a configured `PersistenceReport`.

- [ ] **Step 2: Write failing selection-path tests**

Cover busy, dry-run, and empty behavior:

```python
def test_busy_worker_returns_without_selecting_or_calling_provider() -> None:
    source = Source(lock_acquired=False)
    enrichment = Enrichment()
    persistence = Persistence()

    report = FBNumberRetryService(
        source,
        enrichment,
        persistence,
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    ).run()

    assert report.worker_busy is True
    assert source.list_calls == []
    assert enrichment.calls == []
    assert persistence.calls == []


def test_dry_run_selects_without_enrichment_or_persistence() -> None:
    source = Source(candidates=(candidate(),))
    report = service(source).run(dry_run=True)

    assert report.selected == 1
    assert report.dry_run is True
    assert report.persisted == 0
```

Also assert the normal empty selection returns zero counters.

- [ ] **Step 3: Write failing cooldown and validation tests**

With a fixed clock at `2026-08-09T12:00:00Z`, default selection must pass
`2026-08-08T12:00:00Z`; forced selection must pass `None`. Assert zero/negative
limit and negative cooldown raise `ValueError` before lock acquisition.

- [ ] **Step 4: Write failing execution/report tests**

Provide four candidates whose new statuses are `found`, `not_found`, `failed`,
and `rate_limited`. Return a persistence report with three persisted users and
one identity conflict. Assert:

```python
assert report.selected == 4
assert report.persisted == 3
assert report.found == 1
assert report.not_found == 1
assert report.failed == 1
assert report.rate_limited == 1
assert report.retry_pending == 2
assert report.database_failures == 1
```

Assert each candidate becomes exactly `candidate.to_bundle()` and the worker
lock context exits when enrichment or persistence raises.

- [ ] **Step 5: Run retry service tests and confirm RED**

Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_retry.py -q
```

Expected: import failure because `services.retry` does not exist.

- [ ] **Step 6: Implement narrow protocols and immutable report**

Define:

```python
@dataclass(frozen=True, slots=True)
class RetryReport:
    selected: int = 0
    persisted: int = 0
    found: int = 0
    not_found: int = 0
    failed: int = 0
    rate_limited: int = 0
    database_failures: int = 0
    dry_run: bool = False
    worker_busy: bool = False

    @property
    def retry_pending(self) -> int:
        return self.failed + self.rate_limited

    @property
    def exit_code(self) -> int:
        if self.database_failures:
            return 5
        if self.retry_pending:
            return 1
        return 0
```

Define protocols for the retry source, enrichment runner, and persistence
runner. The retry source protocol returns `ContextManager[bool]` from
`fbnumber_retry_lock()` and typed candidates from
`list_fbnumber_retry_candidates(...)`. Enrichment and persistence constructor
dependencies are optional so a dry-run service can be created without a
provider. A non-dry execution with either dependency missing raises a clear
`ValueError` before processing candidates.

- [ ] **Step 7: Implement `FBNumberRetryService.run`**

Validate arguments first, compute:

```python
eligible_before = None if force else self.clock() - cooldown
```

Acquire the lock, handle busy/dry/empty paths, call
`run_bundles(tuple(candidate.to_bundle() for candidate in candidates))`, then
persist. Count statuses by inspecting every `EnrichedUser.provider_result` so
`failed` and `rate_limited` remain distinct. Copy `persistence.persisted` and
`persistence.db_failed` to the report.

- [ ] **Step 8: Export service types and run focused tests**

Export `FBNumberRetryService` and `RetryReport` from
`fb_data_pipeline.services`. Run:

```powershell
python -m pytest tests/unit/data_pipeline/test_retry.py tests/unit/data_pipeline/test_persistence.py tests/unit/data_pipeline/test_pipeline.py -q
```

Expected: all focused service tests pass.

- [ ] **Step 9: Review checkpoint**

Confirm business rules live in the service/report, not in argparse or SQL
formatting. Confirm lock exit occurs on every path. Do not commit.

---

### Task 4: Pipeline retry CLI

**Files:**

- Modify: `src/fb_crawl/cli/pipeline.py`
- Modify: `tests/unit/cli/test_pipeline_parser.py`
- Modify: `tests/integration/test_pipeline_cli.py`

**Interfaces:**

- Consumes: `PipelineSettings`, `PostgresRepository`, `FBNumberProvider`, `EnrichmentPipeline`, `PipelinePersistenceService`, and `FBNumberRetryService`.
- Produces: `fb-crawl pipeline retry [--limit N] [--cooldown-hours H] [--force] [--dry-run]` plus a stable one-line summary and exit-code mapping.

- [ ] **Step 1: Write failing parser tests**

Add:

```python
def test_pipeline_retry_parser_defaults() -> None:
    args = build_parser().parse_args(["pipeline", "retry"])

    assert args.pipeline_command == "retry"
    assert args.limit == 20
    assert args.cooldown_hours == 24
    assert args.force is False
    assert args.dry_run is False


def test_pipeline_retry_parser_accepts_controls() -> None:
    args = build_parser().parse_args([
        "pipeline", "retry",
        "--limit", "7",
        "--cooldown-hours", "0",
        "--force",
        "--dry-run",
    ])

    assert (args.limit, args.cooldown_hours) == (7, 0)
    assert args.force is True
    assert args.dry_run is True
```

- [ ] **Step 2: Write failing CLI dry-run composition test**

Monkeypatch settings, repository, and retry service. Assert dry-run calls only
`settings.require_database()`, never `settings.require_fb_number()`, never
constructs `FBNumberProvider`, and prints:

```text
selected=2 persisted=0 found=0 not_found=0 failed=0 rate_limited=0 retry_pending=0 database_failures=0 dry_run=true worker_busy=false
```

- [ ] **Step 3: Write failing normal-run cleanup and exit tests**

Use a fake provider with `close_calls`. Parameterize reports for exit `0`, `1`,
and `5`. Assert normal execution requires both database and FBNumber settings,
passes `timedelta(hours=args.cooldown_hours)` to the service, prints every
counter, and returns `report.exit_code`. Assert the provider closes when the
service succeeds, raises `DatabaseError`, or raises `KeyboardInterrupt`; the
interrupt path must return exit `130`.

- [ ] **Step 4: Write failing invalid-value tests**

Assert `--limit 0`, `--limit -1`, and `--cooldown-hours -1` return exit `2`,
print a safe validation message, and construct neither repository nor provider.

- [ ] **Step 5: Run CLI tests and confirm RED**

Run:

```powershell
python -m pytest tests/unit/cli/test_pipeline_parser.py tests/integration/test_pipeline_cli.py -q
```

Expected: retry parser/runtime tests fail because the command is absent.

- [ ] **Step 6: Add the retry parser contract**

Create the subparser with:

```python
retry = commands.add_parser(
    "retry",
    help="Retry durable FBNumber provider failures.",
)
retry.add_argument("--limit", type=int, default=20)
retry.add_argument("--cooldown-hours", type=int, default=24)
retry.add_argument("--force", action="store_true")
retry.add_argument("--dry-run", action="store_true")
```

Keep `migrate` behavior unchanged.

- [ ] **Step 7: Refactor command dispatch into focused functions**

Keep `execute_pipeline(args)` as the public dispatcher and add private
`_execute_migrate(args)` and `_execute_retry(args)` helpers. `_execute_retry`
must:

1. validate `limit > 0` and `cooldown_hours >= 0`;
2. load settings and require the database;
3. create `PostgresRepository` with the configured statement timeout;
4. for dry-run, construct `FBNumberRetryService(repository)` without enrichment
   or persistence dependencies and do not instantiate FBNumber;
5. otherwise require FBNumber, create `FBNumberProvider.from_settings`, wrap it
   in `EnrichmentPipeline`, and use `PipelinePersistenceService(repository)`;
6. close the provider in `finally`;
7. print all report fields as lowercase booleans;
8. return `report.exit_code`.

Allow `ConfigurationError` and `DatabaseError` to reach the existing
application safe-error boundary. Catch `KeyboardInterrupt` around the service
call only to return exit `130`; the provider `finally` cleanup must still run.

- [ ] **Step 8: Run CLI tests and confirm GREEN**

Run:

```powershell
python -m pytest tests/unit/cli/test_pipeline_parser.py tests/integration/test_pipeline_cli.py -q
```

Expected: all pipeline CLI tests pass, including existing migrate behavior.

- [ ] **Step 9: Verify help text manually**

Run:

```powershell
python -m fb_crawl.cli.app pipeline retry --help
```

Expected: help lists `--limit`, `--cooldown-hours`, `--force`, and `--dry-run`.

- [ ] **Step 10: Review checkpoint**

Inspect `src/fb_crawl/cli/pipeline.py` for focused helper boundaries and ensure
no secret values enter summaries or safe errors. Do not commit.

---

### Task 5: Live PostgreSQL behavior and operator documentation

**Files:**

- Modify: `tests/integration/data_pipeline/test_postgres_repository.py`
- Modify: `README.md`
- Modify: `docs/postgresql.md`
- Modify: `docs/future-data-pipeline.md`

**Interfaces:**

- Consumes: completed repository/service/CLI contracts from Tasks 1–4.
- Produces: verified real PostgreSQL selection/locking/persistence behavior and operator-facing commands.

- [ ] **Step 1: Write real latest-attempt and cooldown tests**

Seed attempts through `save_enriched_user` and direct timestamp-specific helper
values. Cover:

- old `failed`, newer `found`: no candidate;
- old `found`, newer expired `rate_limited`: one candidate;
- retryable attempt newer than cutoff: no candidate;
- equal timestamps where the higher attempt ID is terminal: no candidate;
- profile-URL-only user with failed attempt: no candidate;
- forced `eligible_before=None`: newest retryable attempt is selected.

- [ ] **Step 2: Write real advisory-lock test**

Use two repository instances against the dedicated `_test` database:

```python
with first.fbnumber_retry_lock() as first_acquired:
    assert first_acquired is True
    with second.fbnumber_retry_lock() as second_acquired:
        assert second_acquired is False

with second.fbnumber_retry_lock() as acquired_after_release:
    assert acquired_after_release is True
```

- [ ] **Step 3: Write real retry persistence test**

Seed one failed user, select it, return a static `ProviderStatus.FOUND` with a
valid FBNumber phone, run `FBNumberRetryService`, and assert:

- report: selected `1`, persisted `1`, found `1`, pending `0`;
- `facebook_user_phone_slots.phone_1` contains the provider phone;
- the newest attempt is `found`;
- existing profile fields and `phone_2` are unchanged.

Add a repeated-failure variant asserting the new failed attempt becomes latest
and is excluded until the next cooldown.

- [ ] **Step 4: Run live tests and confirm behavior**

Use only the dedicated database ending in `_test`:

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@127.0.0.1:5432/fb_pipeline_test"
python -m pytest tests/integration/data_pipeline/test_postgres_repository.py -q
```

Expected: all live PostgreSQL tests execute and pass; no migration `003` is
created.

- [ ] **Step 5: Update operator documentation**

Document:

```powershell
fb-crawl pipeline retry --limit 20 --cooldown-hours 24
fb-crawl pipeline retry --dry-run
fb-crawl pipeline retry --force --limit 20
```

State that only latest `failed/rate_limited` FBNumber attempts are eligible,
`not_found` is terminal, one worker runs per database, dry-run requires only
`DATABASE_URL`, normal retry requires `FB_NUMBER_API_TOKEN`, and exit codes are
`0`, `1`, `2`, `5`, and `130` as defined in the spec. Update
`future-data-pipeline.md` to mark durable provider retry complete while leaving
the general job API/WebUI queue as future work.

- [ ] **Step 6: Run the entire data-pipeline and CLI regression set**

Run:

```powershell
python -m pytest tests/unit/data_pipeline tests/unit/cli/test_pipeline_parser.py tests/integration/test_pipeline_cli.py tests/integration/data_pipeline/test_postgres_repository.py -q
```

Expected: all tests pass with the live database enabled.

- [ ] **Step 7: Run full verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
python -m fb_crawl.cli.app pipeline retry --help
git diff --check
git status --short
```

Expected: full suite passes, compilation and dependency checks exit zero, help
shows the retry controls, diff check is clean, and only intended uncommitted
files appear in status.

- [ ] **Step 8: Final review checkpoint**

Compare the implementation to
`docs/superpowers/specs/2026-08-09-fbnumber-durable-retry-design.md`. Confirm
every spec rule is covered and no commit, push, cleanup, runtime deletion, job
table, or migration change occurred.

---

## Completion checklist

- [ ] Candidate selection uses only the latest FBNumber attempt per user.
- [ ] `failed` and `rate_limited` retry; `found` and `not_found` do not.
- [ ] UID-or-username identity filtering is enforced in PostgreSQL.
- [ ] Default cooldown is 24 hours and default limit is 20.
- [ ] `--force` bypasses cooldown only.
- [ ] `--dry-run` performs no provider call or enrichment write.
- [ ] The advisory lock is non-blocking, session-level, and released safely.
- [ ] Provider HTTP executes outside database transactions.
- [ ] Every actual retry outcome is persisted as a new attempt.
- [ ] Successful provider values populate `phone_1` without altering `phone_2`.
- [ ] Summary and exit codes follow the approved spec.
- [ ] Existing migrate and authenticated persistence behavior remains green.
- [ ] Full suite and live PostgreSQL tests pass.
- [ ] No migration, job table, commit, push, or artifact deletion occurs.
