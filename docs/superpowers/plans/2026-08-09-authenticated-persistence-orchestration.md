# Authenticated Persistence Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, in-memory `fb_crawl -> FBNumber -> PostgreSQL` persistence to authenticated members, comments, friends, followers, and reactions commands.

**Architecture:** Extend the existing typed enrichment and repository layers rather than transporting data through CSV. A reusable ingestion service combines `EnrichmentPipeline` and `PipelinePersistenceService`; the authenticated CLI only validates flags, composes dependencies, controls optional output, and maps structured reports to summaries and exit codes.

**Tech Stack:** Python 3.11+, argparse, dataclasses, httpx, psycopg 3, PostgreSQL 17, pytest.

## Global Constraints

- PostgreSQL is the source of truth; no transient CSV is created for `--persist` unless `--keep-output` is present.
- Only `members`, `comments`, `friends`, `followers`, and `reactions` accept persistence flags in this phase.
- Existing authenticated behavior without `--persist` must remain unchanged.
- FBNumber calls occur before and outside PostgreSQL transactions.
- A user identity conflict is isolated; database connection/driver errors stop the run.
- Cache, checkpoint, target, session, and pre-existing output files are never deleted.
- Tokens, DSNs, SQL parameters, and raw provider responses must not be printed or stored.
- Do not commit, merge, push, or create a worktree during this implementation.

---

## File map

- Create `src/fb_data_pipeline/services/ingestion.py`: reusable typed composition of enrichment and persistence.
- Modify `src/fb_data_pipeline/services/persistence.py`: isolate identity conflicts and report failures.
- Modify `src/fb_data_pipeline/repositories/postgres.py`: map psycopg/OSError failures to safe `DatabaseError`.
- Modify `src/fb_data_pipeline/services/__init__.py`: export persistence report types without eagerly importing ingestion (which would create an importer/pipeline cycle).
- Modify `src/fb_crawl/cli/authenticated.py`: flags, dependency composition, output policy, summary, and exit codes.
- Modify `tests/unit/data_pipeline/test_persistence.py`: persistence continuation and fail-fast contract.
- Create `tests/unit/data_pipeline/test_ingestion.py`: reusable ingestion service contract.
- Modify `tests/unit/cli/test_authenticated_parser.py`: parser scope and defaults.
- Modify `tests/integration/test_authenticated_cli.py`: CLI orchestration and resource lifecycle.
- Modify `README.md` and `docs/authenticated-cli.md`: operator documentation.

### Task 1: Resilient per-user persistence report

**Files:**
- Modify: `src/fb_data_pipeline/services/persistence.py`
- Modify: `src/fb_data_pipeline/services/__init__.py`
- Test: `tests/unit/data_pipeline/test_persistence.py`

**Interfaces:**
- Consumes: `PipelineRun`, `EnrichedUserRepository.save_enriched_user(EnrichedUser) -> int`, `DatabaseIdentityConflict.code`, and `FacebookIdentity.aliases`.
- Produces: `PersistenceFailure(aliases: tuple[str, ...], error_code: str)` and `PersistenceReport(..., failures: tuple[PersistenceFailure, ...])` with `db_failed` property.

- [ ] **Step 1: Write failing conflict-isolation tests**

Add a repository fake that raises `DatabaseIdentityConflict` at a selected
position and assert that the service saves later users:

```python
def test_persistence_isolates_identity_conflict_and_continues() -> None:
    run = make_run((ProviderStatus.FOUND,) * 3)
    repository = RecordingRepository(
        (101, 103),
        conflict_at=2,
    )

    report = PipelinePersistenceService(repository).persist(run)

    assert repository.saved == list(run.users)
    assert report.persisted == 2
    assert report.db_failed == 1
    assert report.user_ids == (101, 103)
    assert report.failures[0].error_code == "database_identity_conflict"
    assert report.failures[0].aliases == run.users[1].bundle.identity.aliases
```

Keep the existing generic repository-error test and assert it still stops at
the failing user.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/data_pipeline/test_persistence.py -q
```

Expected: failure because `PersistenceFailure`, `failures`, and `db_failed` do
not exist and identity conflicts are not caught.

- [ ] **Step 3: Implement the minimal resilient report**

Add:

```python
@dataclass(frozen=True, slots=True)
class PersistenceFailure:
    aliases: tuple[str, ...]
    error_code: str


@dataclass(frozen=True, slots=True)
class PersistenceReport:
    intended: int
    persisted: int
    provider_retries_required: int
    user_ids: tuple[int, ...]
    failures: tuple[PersistenceFailure, ...] = ()

    @property
    def db_failed(self) -> int:
        return len(self.failures)
```

In `persist`, catch only `DatabaseIdentityConflict`, append a
`PersistenceFailure`, and continue. All other exceptions propagate unchanged.
Count provider retries from the full `PipelineRun`, including users whose
identity transaction failed.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 command. Expected: all persistence tests pass.

### Task 2: Safe repository driver errors

**Files:**
- Modify: `src/fb_data_pipeline/repositories/postgres.py`
- Test: `tests/unit/data_pipeline/test_postgres_repository.py`

**Interfaces:**
- Consumes: `psycopg.Error`, `DatabaseError`, and existing per-user transaction.
- Produces: `PostgresRepository.save_enriched_user(...)` that preserves existing `DatabaseError` subclasses and converts psycopg/OSError failures to `DatabaseError("Database operation failed.")`.

- [ ] **Step 1: Write failing safe-error tests**

Use a connection factory that raises `psycopg.OperationalError("secret DSN")`
and assert:

```python
with pytest.raises(DatabaseError) as captured:
    repository.save_enriched_user(make_enriched())

assert captured.value.safe_message == "Database operation failed."
assert "secret DSN" not in captured.value.safe_message
```

Add a second test proving an explicitly raised `DatabaseIdentityConflict`
passes through unchanged.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: the raw psycopg error escapes.

- [ ] **Step 3: Wrap the transaction boundary**

Wrap the body of `save_enriched_user`:

```python
try:
    # existing connection/cursor transaction
except DatabaseError:
    raise
except (psycopg.Error, OSError) as error:
    raise DatabaseError("Database operation failed.") from error
```

Do not catch arbitrary programming errors.

- [ ] **Step 4: Run repository tests and verify GREEN**

Run the Task 2 command. Expected: all repository tests pass.

### Task 3: Reusable in-memory ingestion service

**Files:**
- Create: `src/fb_data_pipeline/services/ingestion.py`
- Create: `tests/unit/data_pipeline/test_ingestion.py`

**Interfaces:**
- Consumes: `ScrapeResult[UserRecord]`, `EnrichmentPipeline.run_scrape_result`, and `PipelinePersistenceService.persist`.
- Produces: `IngestionReport(pipeline: PipelineReport, persistence: PersistenceReport)` and `AuthenticatedIngestionService.ingest(result, *, default_country_code="84") -> IngestionReport`.

- [ ] **Step 1: Write failing orchestration tests**

Use recording fakes and assert exact call order and identity preservation:

```python
report = service.ingest(scrape_result, default_country_code="84")

assert enrichment.calls == [(scrape_result, "84")]
assert persistence.calls == [pipeline_run]
assert report.pipeline is pipeline_run.report
assert report.persistence is persistence_report
```

Add convenience properties:

```python
assert report.has_provider_retries is True
assert report.has_database_failures is False
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/data_pipeline/test_ingestion.py -q
```

Expected: import failure because the ingestion module does not exist.

- [ ] **Step 3: Implement the service**

Implement small protocols for the two collaborators and:

```python
@dataclass(frozen=True, slots=True)
class IngestionReport:
    pipeline: PipelineReport
    persistence: PersistenceReport

    @property
    def has_provider_retries(self) -> bool:
        return self.persistence.provider_retries_required > 0

    @property
    def has_database_failures(self) -> bool:
        return self.persistence.db_failed > 0


class AuthenticatedIngestionService:
    def ingest(self, result, *, default_country_code="84"):
        run = self.enrichment.run_scrape_result(
            result,
            default_country_code=default_country_code,
        )
        persisted = self.persistence.persist(run)
        return IngestionReport(run.report, persisted)
```

Keep provider/client lifecycle outside this service.
Import these types from `fb_data_pipeline.services.ingestion`; do not eagerly
re-export this pipeline-dependent module from `services.__init__`.

- [ ] **Step 4: Run data-pipeline unit tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/data_pipeline -q
```

Expected: all data-pipeline unit tests pass.

### Task 4: Authenticated parser and persistence dependency composition

**Files:**
- Modify: `src/fb_crawl/cli/authenticated.py`
- Modify: `tests/unit/cli/test_authenticated_parser.py`
- Modify: `tests/integration/test_authenticated_cli.py`

**Interfaces:**
- Consumes: `PipelineSettings`, `FBNumberProvider`, `EnrichmentPipeline`, `PipelinePersistenceService`, `PostgresRepository`, and `AuthenticatedIngestionService`.
- Produces: supported action flags, `AuthenticatedPersistenceRuntime.ingest(...)`, `close()`, and `_load_persistence_runtime()`.

- [ ] **Step 1: Write failing parser-scope tests**

For each supported action, parse `--persist --keep-output` and assert both
values are true. For `profile`, `engagement`, `batch`, `messages`, `inspect`,
and `repair`, assert argparse rejects `--persist`.

Add validation tests proving these fail before browser/runtime creation:

```text
--keep-output without --persist
--persist --output PATH without --keep-output
```

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/cli/test_authenticated_parser.py `
  tests/integration/test_authenticated_cli.py -q
```

Expected: argparse does not know the new flags.

- [ ] **Step 3: Add scoped flags and validation**

Add `_persistence_options(parser)` and invoke it only for `members`,
`comments`, `friends`, `followers`, and `reactions`:

```python
parser.add_argument("--persist", action="store_true")
parser.add_argument("--keep-output", action="store_true")
```

Add `_validate_persistence_args(args)` before browser startup. Use `getattr`
so unsupported actions retain their existing Namespace shape. Raise safe
`ValidationError` messages for invalid combinations.

- [ ] **Step 4: Add the injectable runtime composition**

Define:

```python
@dataclass(frozen=True, slots=True)
class AuthenticatedPersistenceRuntime:
    ingest_result: Callable[[ScrapeResult[UserRecord]], IngestionReport]
    close: Callable[[], None]
```

`_load_persistence_runtime()` loads and validates pipeline settings, constructs
the FBNumber provider, repository with statement timeout, pipeline and
persistence services, and returns closures. Convert missing pipeline settings
to a safe pipeline error with exit code 5. Never include secret values in the
message.

- [ ] **Step 5: Run parser/integration tests and verify GREEN**

Run the Task 4 command. Expected: all selected tests pass.

### Task 5: CLI execution, output policy, summaries, and exit codes

**Files:**
- Modify: `src/fb_crawl/cli/authenticated.py`
- Modify: `src/fb_data_pipeline/repositories/errors.py` or create focused pipeline error beside data-pipeline services
- Modify: `tests/integration/test_authenticated_cli.py`

**Interfaces:**
- Consumes: `AuthenticatedPersistenceRuntime`, `IngestionReport`, existing crawl result and exit logic.
- Produces: output-free persistence by default, optional compatibility output, deterministic pipeline summary fields, and approved exit precedence.

- [ ] **Step 1: Write failing execution tests**

Add tests for:

- `--persist` calls ingestion with the exact in-memory `ScrapeResult` and never
  calls the exporter;
- `--persist --keep-output` calls exporter before ingestion;
- pipeline settings failure occurs before Firefox creation;
- persistence runtime and browser both close on success, provider failure,
  database failure, and interruption;
- provider `not_found` returns 0, provider retry status returns 1, isolated
  identity conflict returns 5, connection error returns 5, and interruption
  takes precedence with 130;
- summary contains all seven pipeline/persistence counts and
  `output=not_requested` when no artifact is written.

- [ ] **Step 2: Run focused integration tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/integration/test_authenticated_cli.py -q
```

Expected: the existing execution path always exports and never ingests.

- [ ] **Step 3: Implement execution ordering**

In `execute_authenticated`:

1. validate persistence flags;
2. build/validate the Facebook request;
3. create and validate persistence runtime before Firefox when requested;
4. crawl;
5. export only for legacy mode or `--keep-output`;
6. ingest the same result object when `--persist`;
7. append structured counts;
8. compute exit precedence;
9. close pipeline runtime and browser independently in `finally`.

Do not write or delete an output artifact in the default persistence path.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 5 command. Expected: all authenticated CLI integration tests pass.

### Task 6: Live PostgreSQL proof and operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/authenticated-cli.md`
- Modify: `tests/integration/data_pipeline/test_postgres_repository.py` only if an existing assertion cannot prove the end state

**Interfaces:**
- Consumes: completed CLI/service contract and `facebook_user_phone_slots` view.
- Produces: documented commands and verified authoritative records.

- [ ] **Step 1: Update documentation**

Document environment requirements, migration prerequisite, both CLI examples,
phone slot meaning, no-output default, safe failure behavior, and that cache,
session, checkpoint, and existing artifacts are retained.

- [ ] **Step 2: Run focused unit and integration suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/data_pipeline tests/unit/cli/test_authenticated_parser.py `
  tests/integration/test_authenticated_cli.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run live PostgreSQL integration**

With the repository test database only:

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/integration/data_pipeline/test_postgres_repository.py -q
```

Expected: all PostgreSQL integration tests pass. Never point this command at
the development/source-of-truth database because the fixture truncates tables.

- [ ] **Step 4: Run complete verification**

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\fb-crawl.exe authenticated friends --help
git diff --check
git status --short
```

Expected: full suite green, no broken dependencies, persistence flags visible
only on supported commands, and only intentional uncommitted files remain.
