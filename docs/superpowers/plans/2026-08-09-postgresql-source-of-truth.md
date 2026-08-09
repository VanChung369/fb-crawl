# PostgreSQL Source-of-Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every enriched Facebook identity, all phone evidence, and every FBNumber attempt into regular PostgreSQL 17, with `phone_1` and `phone_2` exposed by a deterministic read-only view.

**Architecture:** `fb_crawl` continues to collect Facebook data and `fb_data_pipeline` continues to normalize and enrich it before persistence. Provider HTTP calls finish before persistence begins. `PostgresRepository` owns one short transaction per user, resolves identity under sorted advisory locks, upserts normalized data, records the provider attempt, and commits atomically. PostgreSQL is authoritative; CSV cleanup remains explicitly deferred.

**Tech Stack:** Python 3.12, psycopg 3, PostgreSQL 17, Docker Compose, pytest.

## Global Constraints

- Work only in `D:/project/fb/fb-crawl`.
- Use regular PostgreSQL and `psycopg`; do not add Supabase services, SDKs, auth, or APIs.
- Keep `fb_data_pipeline` beside `fb_crawl` under the existing `src/` directory.
- `phone_1` is derived from evidence whose origin is `fbnumber`.
- `phone_2` is derived from evidence whose origin is `fb_crawl`.
- Never store or log `DATABASE_URL`, FBNumber tokens, raw request payloads, or raw provider bodies.
- Do not call FBNumber or any other HTTP service inside a database transaction.
- Persist one enriched user per short transaction. Stop the run after an identity conflict or PostgreSQL error; earlier committed users remain valid.
- Acquire transaction advisory locks for sorted, non-empty identity aliases before resolving a user.
- Keep evidence insertion order stable.
- Do not delete CSV, JSON, XLSX, target, session, or cache artifacts in this plan.
- Add production behavior only after its focused test has failed for the expected reason.
- Stage and commit only the files named in the current task; preserve unrelated worktree changes.

## File Map

### Create

- `compose.yaml`: local PostgreSQL 17 development service.
- `src/fb_data_pipeline/migrations/__init__.py`: packaged migration discovery and checksums.
- `src/fb_data_pipeline/migrations/001_initial.sql`: normalized schema, indexes, constraints, and `facebook_user_phone_slots` view.
- `src/fb_data_pipeline/repositories/__init__.py`: public persistence exports.
- `src/fb_data_pipeline/repositories/errors.py`: safe database and identity-conflict errors.
- `src/fb_data_pipeline/repositories/migrations.py`: idempotent migration runner.
- `src/fb_data_pipeline/repositories/postgres.py`: PostgreSQL user/evidence/attempt repository.
- `src/fb_data_pipeline/services/persistence.py`: sequential run persistence and reporting.
- `src/fb_crawl/cli/pipeline.py`: `pipeline migrate` command.
- `tests/unit/data_pipeline/test_migrations.py`: migration resource contract.
- `tests/unit/data_pipeline/test_migration_runner.py`: runner order, idempotency, and checksum tests.
- `tests/unit/data_pipeline/test_postgres_repository.py`: transaction and upsert orchestration tests with fakes.
- `tests/unit/data_pipeline/test_persistence.py`: stop-on-error and reporting tests.
- `tests/unit/cli/test_pipeline_parser.py`: parser contract.
- `tests/integration/test_pipeline_cli.py`: CLI migration delegation and safe errors.
- `tests/integration/data_pipeline/test_postgres_repository.py`: opt-in live PostgreSQL behavior.
- `docs/postgresql.md`: local setup, migration, security, and verification guide.

### Modify

- `pyproject.toml`: add psycopg runtime dependency.
- `.env.example`: document PostgreSQL development and application settings.
- `src/fb_data_pipeline/config.py`: load and validate database settings separately from FBNumber settings.
- `src/fb_data_pipeline/services/__init__.py`: export persistence service types.
- `src/fb_crawl/cli/app.py`: register and execute `pipeline` mode.
- `README.md`: link PostgreSQL setup and state the source-of-truth boundary.

---

## Task 1: Add PostgreSQL dependency, local service, and configuration

**Files:**

- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `src/fb_data_pipeline/config.py`
- Modify: `tests/unit/data_pipeline/test_config.py`
- Create: `compose.yaml`

- [ ] **Step 1: Write failing database configuration tests**

Append tests that prove database configuration is independent from provider configuration:

```python
def test_pipeline_settings_load_database_contract() -> None:
    settings = load_pipeline_settings(
        {
            "DATABASE_URL": "postgresql://app:password@localhost/fb_pipeline",
            "DATABASE_STATEMENT_TIMEOUT_SECONDS": "7.5",
        }
    )

    assert settings.database_url == (
        "postgresql://app:password@localhost/fb_pipeline"
    )
    assert settings.database_statement_timeout_seconds == 7.5


def test_database_url_is_required_only_when_database_is_started() -> None:
    settings = load_pipeline_settings({})

    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        settings.require_database()


def test_pipeline_settings_reject_invalid_database_timeout() -> None:
    with pytest.raises(
        ConfigurationError,
        match="DATABASE_STATEMENT_TIMEOUT_SECONDS",
    ):
        load_pipeline_settings(
            {"DATABASE_STATEMENT_TIMEOUT_SECONDS": "0"}
        )
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_config.py -q
```

Expected: failures because `PipelineSettings` has no database fields or `require_database()` method.

- [ ] **Step 3: Add the minimal configuration implementation**

Add these fields and validator to `PipelineSettings`:

```python
database_url: str = ""
database_statement_timeout_seconds: float = 5.0

def require_database(self) -> None:
    if not self.database_url:
        raise ConfigurationError("DATABASE_URL is required.")
```

Add these keyword arguments to the `PipelineSettings` returned by `load_pipeline_settings`:

```python
database_url=values.get("DATABASE_URL", "").strip(),
database_statement_timeout_seconds=_positive_float(
    "DATABASE_STATEMENT_TIMEOUT_SECONDS",
    values.get("DATABASE_STATEMENT_TIMEOUT_SECONDS", "5"),
),
```

Change the project dependency list to include:

```toml
"psycopg[binary]>=3.2,<4"
```

Create `compose.yaml` with a local-only PostgreSQL service:

```yaml
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-fb_pipeline}
      POSTGRES_USER: ${POSTGRES_USER:-fb_pipeline}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-fb_pipeline_dev}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 5s
      retries: 10
    volumes:
      - fb_pipeline_postgres:/var/lib/postgresql/data

volumes:
  fb_pipeline_postgres:
```

Document these values in `.env.example` without adding real credentials:

```dotenv
DATABASE_URL=postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline
DATABASE_STATEMENT_TIMEOUT_SECONDS=5
POSTGRES_DB=fb_pipeline
POSTGRES_USER=fb_pipeline
POSTGRES_PASSWORD=fb_pipeline_dev
POSTGRES_PORT=5432
```

- [ ] **Step 4: Install the editable project and confirm GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser,xlsx]"
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_config.py -q
```

Expected: all configuration tests pass.

- [ ] **Step 5: Commit the configuration slice**

```powershell
git add pyproject.toml .env.example compose.yaml src/fb_data_pipeline/config.py tests/unit/data_pipeline/test_config.py
git commit -m "feat: configure PostgreSQL pipeline"
```

---

## Task 2: Package the initial normalized schema and preferred-slot view

**Files:**

- Create: `src/fb_data_pipeline/migrations/__init__.py`
- Create: `src/fb_data_pipeline/migrations/001_initial.sql`
- Create: `tests/unit/data_pipeline/test_migrations.py`

- [ ] **Step 1: Write failing migration discovery tests**

Create tests against public behavior rather than filesystem paths:

```python
from fb_data_pipeline.migrations import load_migrations


def test_initial_migration_is_packaged_with_stable_checksum() -> None:
    migrations = load_migrations()

    assert [item.version for item in migrations] == ["001_initial"]
    assert len(migrations[0].checksum) == 64
    assert "CREATE TABLE facebook_users" in migrations[0].sql
    assert "CREATE TABLE phone_numbers" in migrations[0].sql
    assert "CREATE TABLE user_phone_evidence" in migrations[0].sql
    assert "CREATE TABLE enrichment_attempts" in migrations[0].sql
    assert "CREATE VIEW facebook_user_phone_slots" in migrations[0].sql


def test_migrations_are_sorted_by_version() -> None:
    assert tuple(
        item.version for item in load_migrations()
    ) == tuple(
        sorted(item.version for item in load_migrations())
    )
```

- [ ] **Step 2: Run the focused test and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_migrations.py -q
```

Expected: import failure because the migrations package does not exist.

- [ ] **Step 3: Implement packaged migration discovery**

Create an immutable migration value and load every numeric SQL resource in version order:

```python
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    sql: str
    checksum: str


def load_migrations() -> tuple[Migration, ...]:
    resources = files(__package__)
    migrations: list[Migration] = []
    for resource in sorted(resources.iterdir(), key=lambda item: item.name):
        if resource.suffix != ".sql":
            continue
        sql = resource.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=resource.stem,
                sql=sql,
                checksum=sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(migrations)
```

- [ ] **Step 4: Write `001_initial.sql` with the exact schema contract**

The migration must create:

1. `facebook_users` with identity primary key, nullable unique UID, nullable display username, nullable unique normalized username, nullable display name, nullable unique canonical profile URL, timestamps, and a check requiring at least one usable identity alias.
2. `phone_numbers` with identity primary key, unique normalized phone, first-seen display phone, timestamp, and `CHECK (normalized_phone ~ '^\\+[0-9]{8,15}$')`.
3. `user_phone_evidence` with foreign keys, `origin IN ('fbnumber', 'fb_crawl')`, safe provenance fields, confidence, capture times, positive evidence count, timestamps, and unique key `(facebook_user_id, phone_number_id, origin, source, source_url, provider)`.
4. `enrichment_attempts` with a user foreign key, provider, constrained status, checked time, correlation ID, safe error code, and non-negative result count.
5. Indexes on every foreign key plus:
   - `(facebook_user_id, origin, confidence, last_captured_at DESC)` for evidence lookup;
   - `(facebook_user_id, provider, checked_at DESC)` for provider audit lookup.
6. `facebook_user_phone_slots`, implemented with `row_number()` partitions by user and origin:
   - FBNumber order: `last_captured_at DESC`, then evidence ID descending;
   - crawler order: confidence rank `profile_field`, `strong_pattern`, `weak_pattern`, `unknown`, then `last_captured_at DESC`, then evidence ID descending.

Use `NULLIF(btrim(value), '')` at repository boundaries rather than database triggers. Use `ON DELETE CASCADE` for evidence and attempts linked to a deleted user and `ON DELETE RESTRICT` for referenced phone rows.

Define `origin`, `source`, `source_url`, `provider`, `correlation_id`, and
`error_code` as `text NOT NULL DEFAULT ''` where a value may be absent. This
keeps the evidence unique key deterministic instead of allowing duplicate rows
through PostgreSQL's distinct-`NULL` unique behavior.

- [ ] **Step 5: Run the focused tests and package build**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_migrations.py -q
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir runtime\wheel-check
.\.venv\Scripts\python.exe -c "import glob, zipfile; path = glob.glob('runtime/wheel-check/*.whl')[0]; print('\n'.join(name for name in zipfile.ZipFile(path).namelist() if 'migrations/' in name))"
```

Add this exact package-data configuration to `pyproject.toml` before building:

```toml
[tool.setuptools.package-data]
fb_data_pipeline = ["migrations/*.sql"]
```

Inspect the wheel listing and confirm
`fb_data_pipeline/migrations/001_initial.sql` is present. Expected: tests pass
and the SQL resource is packaged.

- [ ] **Step 6: Commit the schema slice**

```powershell
git add pyproject.toml src/fb_data_pipeline/migrations tests/unit/data_pipeline/test_migrations.py
git commit -m "feat: add PostgreSQL pipeline schema"
```

---

## Task 3: Implement safe, idempotent migration execution

**Files:**

- Create: `src/fb_data_pipeline/repositories/__init__.py`
- Create: `src/fb_data_pipeline/repositories/errors.py`
- Create: `src/fb_data_pipeline/repositories/migrations.py`
- Create: `tests/unit/data_pipeline/test_migration_runner.py`

- [ ] **Step 1: Write failing runner tests with a scripted fake connection**

Cover these observable cases:

- the runner creates `schema_migrations(version, checksum, applied_at)` before reading applied migrations;
- unapplied migrations execute in version order and return their versions;
- an already-applied matching checksum is skipped and returns no applied version;
- an already-applied changed checksum raises a safe `MigrationChecksumError` without exposing SQL or the DSN;
- a SQL failure exits the transaction context with the original exception and never records that version.

The primary assertion should be command order, for example:

```python
runner = MigrationRunner(
    "postgresql://hidden",
    connect_factory=fake_connect,
    migrations=(migration,),
)

assert runner.apply() == ("001_initial",)
assert connection.committed is True
assert connection.commands[-2][0] == migration.sql
assert "INSERT INTO schema_migrations" in connection.commands[-1][0]
assert connection.commands[-1][1] == (
    migration.version,
    migration.checksum,
)
```

- [ ] **Step 2: Run the focused test and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_migration_runner.py -q
```

Expected: import failure because repository modules do not exist.

- [ ] **Step 3: Add safe repository errors**

Define errors deriving from `FbCrawlError`:

```python
class DatabaseError(FbCrawlError):
    code = "database_error"
    exit_code = 5


class DatabaseIdentityConflict(DatabaseError):
    code = "database_identity_conflict"


class MigrationChecksumError(DatabaseError):
    code = "database_migration_checksum_mismatch"
```

Messages must identify only the safe migration version or Facebook identity alias; they must not contain the DSN, SQL text, SQL parameters, or provider token.

- [ ] **Step 4: Implement `MigrationRunner`**

Use this public constructor and method:

```python
class MigrationRunner:
    def __init__(
        self,
        database_url: str,
        *,
        connect_factory=psycopg.connect,
        migrations: tuple[Migration, ...] | None = None,
    ) -> None:
        self.database_url = database_url
        self.connect_factory = connect_factory
        self.migrations = load_migrations() if migrations is None else migrations

    def apply(self) -> tuple[str, ...]:
        applied: list[str] = []
        with self.connect_factory(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version text PRIMARY KEY,
                        checksum text NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute(
                    "SELECT version, checksum FROM schema_migrations"
                )
                existing = dict(cursor.fetchall())
                for migration in self.migrations:
                    previous = existing.get(migration.version)
                    if previous == migration.checksum:
                        continue
                    if previous is not None:
                        raise MigrationChecksumError(
                            "Applied database migration checksum changed: "
                            f"{migration.version}."
                        )
                    cursor.execute(migration.sql)
                    cursor.execute(
                        """
                        INSERT INTO schema_migrations (version, checksum)
                        VALUES (%s, %s)
                        """,
                        (migration.version, migration.checksum),
                    )
                    applied.append(migration.version)
        return tuple(applied)
```

Let the connection context commit on success and rollback on any exception. Do not catch and interpolate psycopg exceptions here; CLI translation is tested in Task 6.

- [ ] **Step 5: Run focused tests and static compilation**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_migration_runner.py -q
.\.venv\Scripts\python.exe -m compileall -q src/fb_data_pipeline
```

Expected: runner tests pass and compilation exits zero.

- [ ] **Step 6: Commit the migration runner**

```powershell
git add src/fb_data_pipeline/repositories tests/unit/data_pipeline/test_migration_runner.py
git commit -m "feat: run packaged PostgreSQL migrations"
```

---

## Task 4: Persist one enriched user atomically

**Files:**

- Create: `src/fb_data_pipeline/repositories/postgres.py`
- Modify: `src/fb_data_pipeline/repositories/__init__.py`
- Create: `tests/unit/data_pipeline/test_postgres_repository.py`

- [ ] **Step 1: Write failing repository orchestration tests**

Build `EnrichedUser` fixtures from the real model classes and use a scripted fake connection. Test these behaviors separately:

1. aliases are sorted before `pg_advisory_xact_lock(hashtextextended(alias, 0))` calls;
2. the local statement timeout is set with `SELECT set_config('statement_timeout', %s, true)` before identity work;
3. zero matches inserts a user and returns its bigint ID;
4. one match updates only non-empty incoming identity fields;
5. aliases matching two user IDs raise `DatabaseIdentityConflict` before phone writes;
6. phone rows are upserted by normalized number;
7. evidence origin is `fbnumber` for `PhoneSlot.PHONE_1` and `fb_crawl` for `PhoneSlot.PHONE_2`;
8. repeated evidence increments `evidence_count` and retains the first capture time while updating the last capture time and strongest confidence;
9. every provider status writes one `enrichment_attempts` row, including `not_found`, `rate_limited`, and `failed` with zero evidence;
10. evidence is processed in sorted order by normalized phone, origin, source, source URL, and provider;
11. any exception causes the connection context to roll back and no later SQL executes.

The fixture should prove both phone slots are preserved:

```python
enriched = EnrichedUser(
    bundle=UserBundle(
        identity=FacebookIdentity(
            uid="100013347102233",
            username="thang.duc.961556",
            name="Bui Duc Thang",
            profile_url="https://www.facebook.com/thang.duc.961556",
        ),
        evidence=(
            PhoneEvidence(
                phone_number="0912345678",
                normalized_phone="+84912345678",
                source="profile_about",
                source_url=(
                    "https://www.facebook.com/"
                    "thang.duc.961556/about_contact_and_basic_info"
                ),
                confidence="profile_field",
            ),
            PhoneEvidence(
                phone_number="0987654321",
                normalized_phone="+84987654321",
                source="external:fbnumber",
                provider="fbnumber",
                confidence="provider_result",
            ),
        ),
    ),
    provider_result=ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FOUND,
        correlation_id="request-123",
    ),
)
```

- [ ] **Step 2: Run the focused test and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: import failure because `PostgresRepository` does not exist.

- [ ] **Step 3: Implement normalization helpers and public repository API**

Use this public shape:

```python
class PostgresRepository:
    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(
            1,
            round(statement_timeout_seconds * 1000),
        )
        self.connect_factory = connect_factory

    def save_enriched_user(self, enriched: EnrichedUser) -> int:
        if not enriched.bundle.identity.is_usable:
            raise DatabaseIdentityConflict(
                "Cannot persist a Facebook user without an identity alias."
            )
        with self.connect_factory(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{self.statement_timeout_ms}ms",),
                )
                user_id = self._upsert_user(cursor, enriched.bundle.identity)
                self._upsert_evidence(cursor, user_id, enriched.bundle.evidence)
                self._insert_attempt(
                    cursor,
                    user_id,
                    enriched.provider_result,
                )
        return user_id
```

Use small private methods `_lock_aliases`, `_matching_user_ids`, `_upsert_user`, `_upsert_phone`, `_upsert_evidence`, and `_insert_attempt`. Pass every data value as a psycopg parameter; never construct SQL from identity/provider values.

- [ ] **Step 4: Implement locked identity resolution**

For every `sorted(set(identity.aliases))`, execute:

```sql
SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))
```

Then query matching rows with nullable parameters:

```sql
SELECT id
FROM facebook_users
WHERE (%s IS NOT NULL AND facebook_uid = %s)
   OR (%s IS NOT NULL AND normalized_username = %s)
   OR (%s IS NOT NULL AND profile_url = %s)
FOR UPDATE
```

Deduplicate returned IDs. More than one distinct ID is a conflict. For one ID, update with `COALESCE` so blank incoming values do not erase stored identity. For zero IDs, insert with `ON CONFLICT DO NOTHING RETURNING id`, then rerun the matching query under the same locks. If the second query is not exactly one ID, raise `DatabaseIdentityConflict`.

Normalize incoming values in Python before parameters:

```python
uid = identity.uid or None
username = identity.username or None
normalized_username = identity.username.casefold() or None
name = identity.name or None
profile_url = identity.profile_url or None
```

- [ ] **Step 5: Implement phone, evidence, and attempt upserts**

Upsert a normalized phone and retrieve its ID:

```sql
INSERT INTO phone_numbers (normalized_phone, display_phone)
VALUES (%s, %s)
ON CONFLICT (normalized_phone) DO UPDATE
SET display_phone = phone_numbers.display_phone
RETURNING id
```

Derive `origin` from the typed slot, not from an untrusted source string:

```python
origin = (
    "fbnumber"
    if evidence.slot is PhoneSlot.PHONE_1
    else "fb_crawl"
)
```

Convert missing capture time to the repository clock time in UTC. Upsert evidence with the defined composite unique key. On conflict:

- keep `first_captured_at` as `LEAST(existing, excluded)`;
- set `last_captured_at` as `GREATEST(existing, excluded)`;
- increment `evidence_count` by one;
- set `updated_at = now()`;
- retain the stronger crawler confidence using the exact rank `profile_field > strong_pattern > weak_pattern > unknown`;
- for FBNumber evidence, retain the newest observation's confidence and correlation ID.

Insert an immutable provider-attempt row with `values_found = len(provider_result.evidence)`. Store only `provider`, `status.value`, `checked_at`, `correlation_id`, `error_code`, and count.

- [ ] **Step 6: Run focused repository tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: all transaction-order, origin, upsert, and failure tests pass.

- [ ] **Step 7: Commit the repository**

```powershell
git add src/fb_data_pipeline/repositories tests/unit/data_pipeline/test_postgres_repository.py
git commit -m "feat: persist enriched users in PostgreSQL"
```

---

## Task 5: Persist a completed enrichment run sequentially

**Files:**

- Create: `src/fb_data_pipeline/services/persistence.py`
- Modify: `src/fb_data_pipeline/services/__init__.py`
- Create: `tests/unit/data_pipeline/test_persistence.py`

- [ ] **Step 1: Write failing persistence-service tests**

Test a fake repository with `save_enriched_user` and prove:

- users are persisted in `PipelineRun.users` order;
- the report contains intended, persisted, and provider-retry counts;
- `NOT_FOUND` is a completed attempt and does not count as a retry;
- `RATE_LIMITED` and `FAILED` count as provider retries;
- the service stops immediately when repository persistence raises;
- the exception propagates so artifact cleanup cannot be mistaken for safe.

Use the public report contract:

```python
assert service.persist(run) == PersistenceReport(
    intended=3,
    persisted=3,
    provider_retries_required=1,
    user_ids=(101, 102, 103),
)
```

- [ ] **Step 2: Run the focused test and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_persistence.py -q
```

Expected: import failure because the persistence service does not exist.

- [ ] **Step 3: Implement the service**

Use a protocol so tests do not depend on psycopg:

```python
class EnrichedUserRepository(Protocol):
    def save_enriched_user(self, enriched: EnrichedUser) -> int:
        pass


@dataclass(frozen=True, slots=True)
class PersistenceReport:
    intended: int
    persisted: int
    provider_retries_required: int
    user_ids: tuple[int, ...]


class PipelinePersistenceService:
    def __init__(self, repository: EnrichedUserRepository) -> None:
        self.repository = repository

    def persist(self, run: PipelineRun) -> PersistenceReport:
        user_ids: list[int] = []
        retries = 0
        for enriched in run.users:
            user_ids.append(self.repository.save_enriched_user(enriched))
            if enriched.provider_result.status in {
                ProviderStatus.RATE_LIMITED,
                ProviderStatus.FAILED,
            }:
                retries += 1
        return PersistenceReport(
            intended=len(run.users),
            persisted=len(user_ids),
            provider_retries_required=retries,
            user_ids=tuple(user_ids),
        )
```

Do not catch repository errors and do not add cleanup here.

- [ ] **Step 4: Run focused tests and the complete data-pipeline unit slice**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline -q
```

Expected: all data-pipeline unit tests pass.

- [ ] **Step 5: Commit the orchestration slice**

```powershell
git add src/fb_data_pipeline/services tests/unit/data_pipeline/test_persistence.py
git commit -m "feat: persist enriched pipeline runs"
```

---

## Task 6: Expose database migrations through the existing CLI

**Files:**

- Create: `src/fb_crawl/cli/pipeline.py`
- Modify: `src/fb_crawl/cli/app.py`
- Create: `tests/unit/cli/test_pipeline_parser.py`
- Create: `tests/integration/test_pipeline_cli.py`

- [ ] **Step 1: Write failing parser tests**

Test only public parser behavior:

```python
from fb_crawl.cli.app import build_parser


def test_pipeline_migrate_parser_contract() -> None:
    args = build_parser().parse_args(["pipeline", "migrate"])

    assert args.mode == "pipeline"
    assert args.pipeline_command == "migrate"
```

- [ ] **Step 2: Write failing CLI delegation and safe-error tests**

Monkeypatch settings and `MigrationRunner`, then prove:

- `main(["pipeline", "migrate"])` calls `require_database()` and the runner once;
- stdout is `applied=001_initial` when a migration was applied;
- stdout is `applied=none` when the database is current;
- a connection exception produces only `Database operation failed.` on stderr, returns exit code 5, and contains neither the DSN nor exception details.

- [ ] **Step 3: Run the focused tests and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/cli/test_pipeline_parser.py tests/integration/test_pipeline_cli.py -q
```

Expected: parser rejects `pipeline` because the mode is not registered.

- [ ] **Step 4: Add the `pipeline migrate` parser and executor**

Use the same add/execute structure as existing CLI modules:

```python
def add_pipeline_parser(
    modes: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = modes.add_parser(
        "pipeline",
        help="Manage the PostgreSQL data pipeline.",
    )
    commands = parser.add_subparsers(
        dest="pipeline_command",
        required=True,
    )
    commands.add_parser("migrate", help="Apply database migrations.")
    return parser


def execute_pipeline(args: argparse.Namespace) -> int:
    if args.pipeline_command != "migrate":
        raise ValueError(
            f"Unsupported pipeline command: {args.pipeline_command}"
        )
    settings = load_pipeline_settings()
    settings.require_database()
    runner = MigrationRunner(settings.database_url)
    try:
        applied = runner.apply()
    except (psycopg.Error, OSError) as error:
        raise DatabaseError("Database operation failed.") from error
    names = ",".join(applied) if applied else "none"
    print(f"applied={names}")
    return 0
```

Register `add_pipeline_parser(modes)` in `build_parser()` and route `args.mode == "pipeline"` to `execute_pipeline(args)`.

- [ ] **Step 5: Run focused tests and CLI help smoke tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/cli/test_pipeline_parser.py tests/integration/test_pipeline_cli.py -q
.\.venv\Scripts\fb-crawl.exe pipeline --help
.\.venv\Scripts\fb-crawl.exe pipeline migrate --help
```

Expected: tests pass and both help commands exit zero without requiring a database.

- [ ] **Step 6: Commit the CLI slice**

```powershell
git add src/fb_crawl/cli/app.py src/fb_crawl/cli/pipeline.py tests/unit/cli/test_pipeline_parser.py tests/integration/test_pipeline_cli.py
git commit -m "feat: add PostgreSQL migration command"
```

---

## Task 7: Verify behavior against PostgreSQL 17 and document operations

**Files:**

- Create: `tests/integration/data_pipeline/test_postgres_repository.py`
- Create: `docs/postgresql.md`
- Modify: `README.md`

- [ ] **Step 1: Add opt-in live PostgreSQL tests**

At module scope, skip safely unless `TEST_DATABASE_URL` is explicitly present:

```python
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured",
)
```

Use only a dedicated disposable test database. Apply migrations before each live test module. Clean rows with explicit table names inside a transaction; never drop a database or schema from tests.

Cover these real PostgreSQL behaviors:

1. running migrations twice returns `("001_initial",)` and then `()`;
2. saving the same enriched user twice produces one user, two phone rows, two evidence relationships whose counts are 2, and two attempt rows;
3. later UID/username/profile aliases enrich the same user row;
4. aliases that point to distinct existing rows raise `DatabaseIdentityConflict` and leave evidence/attempt counts unchanged;
5. the view returns FBNumber as `phone_1` and crawler evidence as `phone_2`;
6. newer FBNumber evidence replaces older `phone_1` selection;
7. stronger crawler confidence wins `phone_2` even when weaker evidence is newer;
8. `NOT_FOUND`, `RATE_LIMITED`, and `FAILED` attempts persist the user and crawler evidence with nullable `phone_1`.

- [ ] **Step 2: Confirm the live tests skip cleanly without configuration**

```powershell
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest tests/integration/data_pipeline/test_postgres_repository.py -q
```

Expected: the module is skipped with the configured reason.

- [ ] **Step 3: Start PostgreSQL 17 and run live tests**

```powershell
docker compose up -d postgres
docker compose ps postgres
docker compose exec postgres createdb -U fb_pipeline fb_pipeline_test
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
.\.venv\Scripts\python.exe -m pytest tests/integration/data_pipeline/test_postgres_repository.py -q
```

Run `createdb` only when the dedicated test database does not already exist.
Expected: the service reports healthy and every live integration test passes
against `fb_pipeline_test`, never the development `fb_pipeline` database.

- [ ] **Step 4: Exercise the real migration command**

```powershell
$env:DATABASE_URL = $env:TEST_DATABASE_URL
.\.venv\Scripts\fb-crawl.exe pipeline migrate
.\.venv\Scripts\fb-crawl.exe pipeline migrate
```

Expected first output contains `applied=001_initial`; expected second output is `applied=none`.

- [ ] **Step 5: Document setup and source-of-truth behavior**

In `docs/postgresql.md`, document:

- `docker compose up -d postgres`;
- copying safe development values from `.env.example` into the local environment;
- `fb-crawl pipeline migrate`;
- `TEST_DATABASE_URL` for opt-in tests;
- the four normalized tables and the `facebook_user_phone_slots` view;
- `phone_1 = FBNumber`, `phone_2 = fb_crawl`;
- provider failure still stores crawler data and a safe attempt;
- application and migration roles should be non-superuser and may be separate;
- production should use TLS, rotated secrets, backups, and a transaction-mode pooler;
- CSV cleanup is not implemented in this plan.

Add a short PostgreSQL section to `README.md` linking to this guide and explicitly calling PostgreSQL the source of truth.

- [ ] **Step 6: Run complete verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\fb-crawl.exe --help
git diff --check
git status --short
```

Expected:

- all non-opt-in tests pass;
- live PostgreSQL tests pass when `TEST_DATABASE_URL` is set;
- compilation, dependency check, CLI help, and diff check exit zero;
- only intended task files are modified before the final commit.

- [ ] **Step 7: Commit documentation and live verification**

```powershell
git add tests/integration/data_pipeline/test_postgres_repository.py docs/postgresql.md README.md
git commit -m "test: verify PostgreSQL source of truth"
```

---

## Acceptance Checklist

- [ ] `fb_data_pipeline` remains in the existing `fb-crawl` distribution.
- [ ] The repository uses PostgreSQL 17 and psycopg only; no Supabase dependency exists.
- [ ] Migrations are packaged, checksummed, tracked, and idempotent.
- [ ] Every usable identity can be persisted by UID, normalized username, canonical profile URL, or a compatible combination.
- [ ] Concurrent identity aliases are guarded by sorted transaction advisory locks.
- [ ] All phone evidence and provenance are preserved.
- [ ] `phone_1` is selected from FBNumber evidence.
- [ ] `phone_2` is selected from crawler evidence using confidence then recency.
- [ ] Every provider attempt is auditable without raw payloads or secrets.
- [ ] Provider HTTP is outside database transactions.
- [ ] Persistence stops on identity/database errors while earlier committed users remain valid.
- [ ] No artifact deletion is introduced.
- [ ] The full existing test suite remains green.
