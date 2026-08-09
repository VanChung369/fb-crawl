# Profile Attributes Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve raw Facebook-visible `address`, `birth_date`, and `gender` values through crawler import, enrichment, merge, and PostgreSQL persistence.

**Architecture:** A typed `ProfileData` snapshot belongs to `UserBundle`, separate from identity aliases and phone evidence. A new immutable migration creates `facebook_user_profiles` and extends the existing read view. `PostgresRepository` upserts the snapshot in the same short user transaction while rejecting blank or stale overwrites.

**Tech Stack:** Python 3.12, dataclasses, psycopg 3, PostgreSQL 17, pytest.

## Global Constraints

- Work only in `D:/project/fb/fb-crawl`.
- Persist exactly `address`, `birth_date`, and `gender` in this slice.
- Trim surrounding whitespace and otherwise preserve raw Facebook-visible text.
- Store `birth_date` as PostgreSQL `text`, never as `date`.
- Empty incoming values never erase stored values.
- Newer observed values may replace older values; stale values may not.
- Keep profile data separate from `FacebookIdentity` aliases.
- Do not change `phone_1` or `phone_2` behavior.
- Do not modify `001_initial.sql`; add `002_profile_attributes.sql`.
- Do not call external providers inside database transactions.
- Do not delete CSV, JSON, session, target, cache, or other runtime artifacts.
- Do not commit, merge, push, or create a PR. Leave all changes in the working tree.
- Existing uncommitted profile code is user-owned. Preserve it and validate it; do not delete working code merely to manufacture a RED test.
- For a newly discovered behavior gap, add a focused failing test before changing production code.

## File Map

### Core and pipeline

- Modify: `src/fb_data_pipeline/core/models.py`
- Modify: `src/fb_data_pipeline/importers/crawler.py`
- Modify: `src/fb_data_pipeline/services/merge.py`
- Modify: `src/fb_data_pipeline/services/pipeline.py`
- Test: `tests/unit/data_pipeline/test_models.py`
- Test: `tests/unit/data_pipeline/test_crawler_importer.py`
- Test: `tests/unit/data_pipeline/test_merge.py`
- Test: `tests/unit/data_pipeline/test_pipeline.py`

### PostgreSQL

- Create: `src/fb_data_pipeline/migrations/002_profile_attributes.sql`
- Modify: `src/fb_data_pipeline/repositories/postgres.py`
- Test: `tests/unit/data_pipeline/test_migrations.py`
- Test: `tests/unit/data_pipeline/test_postgres_repository.py`
- Test: `tests/integration/data_pipeline/test_postgres_repository.py`

### Documentation

- Modify: `docs/postgresql.md`
- Modify: `README.md` only if the public schema description lacks the fields.

---

## Task 1: Validate the typed profile snapshot and crawler import

**Files:**

- Modify: `src/fb_data_pipeline/core/models.py`
- Modify: `src/fb_data_pipeline/importers/crawler.py`
- Test: `tests/unit/data_pipeline/test_models.py`
- Test: `tests/unit/data_pipeline/test_crawler_importer.py`

**Interfaces:**

- Consumes: `fb_crawl.core.models.UserRecord` and `PageRecord`.
- Produces: `ProfileData` and `UserBundle.profile` for merge and persistence.

- [ ] **Step 1: Run the existing focused contract tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/data_pipeline/test_models.py `
  tests/unit/data_pipeline/test_crawler_importer.py -q
```

Expected: tests prove trimming, empty defaults, all three user fields, page
address, source URL, and parsed UTC observation time. If a listed behavior is
missing, add one test for that behavior and confirm it fails before production
changes.

- [ ] **Step 2: Ensure the model API has the exact typed shape**

```python
@dataclass(frozen=True, slots=True)
class ProfileData:
    address: str = ""
    birth_date: str = ""
    gender: str = ""
    source_url: str = ""
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", _clean(self.address))
        object.__setattr__(self, "birth_date", _clean(self.birth_date))
        object.__setattr__(self, "gender", _clean(self.gender))
        object.__setattr__(self, "source_url", _clean(self.source_url))

    @property
    def is_empty(self) -> bool:
        return not (self.address or self.birth_date or self.gender)
```

`UserBundle` must preserve existing constructor compatibility:

```python
@dataclass(frozen=True, slots=True)
class UserBundle:
    identity: FacebookIdentity
    evidence: tuple[PhoneEvidence, ...] = ()
    profile: ProfileData = field(default_factory=ProfileData)
```

- [ ] **Step 3: Ensure importer mapping is complete**

For `UserRecord`, construct:

```python
ProfileData(
    address=record.address,
    birth_date=record.birth_date,
    gender=record.gender,
    source_url=record.source_url or record.profile_url,
    observed_at=_first_timestamp(
        record.last_enriched_at,
        record.last_seen,
        record.first_seen,
    ),
)
```

For `PageRecord`, construct:

```python
ProfileData(
    address=record.address,
    source_url=identity.profile_url,
)
```

`_first_timestamp` must try each candidate rather than stopping after an
invalid first string.

- [ ] **Step 4: Re-run focused tests and diff validation**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/data_pipeline/test_models.py `
  tests/unit/data_pipeline/test_crawler_importer.py -q
git diff --check
```

Expected: all focused tests pass and diff check exits zero.

---

## Task 2: Validate deterministic profile merging and enrichment preservation

**Files:**

- Modify: `src/fb_data_pipeline/services/merge.py`
- Modify: `src/fb_data_pipeline/services/pipeline.py`
- Test: `tests/unit/data_pipeline/test_merge.py`
- Test: `tests/unit/data_pipeline/test_pipeline.py`

**Interfaces:**

- Consumes: `ProfileData` from Task 1.
- Produces: `merge_profiles(left, right) -> ProfileData` and an enriched
  `UserBundle` whose profile remains unchanged by FBNumber.

- [ ] **Step 1: Run focused merge and pipeline tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/data_pipeline/test_merge.py `
  tests/unit/data_pipeline/test_pipeline.py -q
```

Expected: tests cover newer non-empty values, blank preservation, timestamped
versus untimestamped values, deterministic first untimestamped value, and
provider enrichment preserving all three fields.

- [ ] **Step 2: Ensure merge precedence is field-specific**

The selection helper must implement:

```python
def _profile_value(
    left: str,
    left_time: datetime | None,
    right: str,
    right_time: datetime | None,
) -> tuple[str, bool]:
    if not right:
        return left, False
    if not left:
        return right, True
    if right_time is not None and left_time is None:
        return right, True
    if left_time is not None and right_time is None:
        return left, False
    if right_time is not None and left_time is not None:
        return (right, True) if right_time >= left_time else (left, False)
    return left, False
```

Apply it independently to `address`, `birth_date`, and `gender`. Set the merged
timestamp to the newest known time. Use the right source URL only if at least
one right-side field was accepted.

- [ ] **Step 3: Ensure bundle and provider merges retain profile data**

`merge_bundles` must include:

```python
profile=merge_profiles(existing.profile, combined.profile),
```

`EnrichmentPipeline._run` must include:

```python
profile=original.profile,
```

when constructing the combined phone bundle.

- [ ] **Step 4: Re-run the data merge slice**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/data_pipeline/test_merge.py `
  tests/unit/data_pipeline/test_pipeline.py -q
```

Expected: all focused tests pass.

---

## Task 3: Validate migration 002 and extended read view

**Files:**

- Create: `src/fb_data_pipeline/migrations/002_profile_attributes.sql`
- Test: `tests/unit/data_pipeline/test_migrations.py`

**Interfaces:**

- Consumes: existing `facebook_users` and `facebook_user_phone_slots` from
  `001_initial.sql`.
- Produces: `facebook_user_profiles` and a backward-compatible extended view.

- [ ] **Step 1: Run migration packaging tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/data_pipeline/test_migrations.py -q
```

Expected migration order:

```python
["001_initial", "002_profile_attributes"]
```

- [ ] **Step 2: Validate the table contract**

`002_profile_attributes.sql` must create exactly one current snapshot row per
user with:

```sql
CREATE TABLE facebook_user_profiles (
    facebook_user_id bigint PRIMARY KEY
        REFERENCES facebook_users (id) ON DELETE CASCADE,
    address text,
    birth_date text,
    gender text,
    source_url text NOT NULL DEFAULT '',
    observed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT facebook_user_profiles_content_required CHECK (
        NULLIF(btrim(address), '') IS NOT NULL
        OR NULLIF(btrim(birth_date), '') IS NOT NULL
        OR NULLIF(btrim(gender), '') IS NOT NULL
    )
);
```

- [ ] **Step 3: Validate the view contract**

The migration must use `CREATE OR REPLACE VIEW
facebook_user_phone_slots`, retain every existing identity/phone column, left
join `facebook_user_profiles`, and add named nullable columns:

```sql
max(profiles.address) AS address,
max(profiles.birth_date) AS birth_date,
max(profiles.gender) AS gender
```

Do not edit `001_initial.sql`; its applied checksum must remain unchanged.

- [ ] **Step 4: Verify package data**

```powershell
.\.venv\Scripts\python.exe -m pip wheel . `
  --no-deps --wheel-dir runtime\wheel-check
.\.venv\Scripts\python.exe -c "import glob, os, zipfile; path=max(glob.glob('runtime/wheel-check/*.whl'), key=os.path.getmtime); names=zipfile.ZipFile(path).namelist(); print([name for name in names if 'migrations/' in name])"
```

Expected: both `001_initial.sql` and `002_profile_attributes.sql` appear in the
wheel.

---

## Task 4: Validate atomic profile persistence

**Files:**

- Modify: `src/fb_data_pipeline/repositories/postgres.py`
- Test: `tests/unit/data_pipeline/test_postgres_repository.py`

**Interfaces:**

- Consumes: `EnrichedUser.bundle.profile` and the schema from Task 3.
- Produces: profile upsert inside `save_enriched_user`'s existing transaction.

- [ ] **Step 1: Run focused repository tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: tests verify profile SQL occurs after identity and before phones,
parameters preserve raw values, empty profiles produce no SQL, and profile SQL
failure rolls back before phone/attempt writes.

- [ ] **Step 2: Ensure transaction order is exact**

`save_enriched_user` must execute:

```python
user_id = self._upsert_user(cursor, enriched.bundle.identity)
self._upsert_profile(cursor, user_id, enriched.bundle.profile)
self._upsert_evidence(cursor, user_id, enriched.bundle.evidence)
self._insert_attempt(cursor, user_id, enriched.provider_result)
```

No provider HTTP call belongs in this method.

- [ ] **Step 3: Validate non-destructive upsert rules**

`_upsert_profile` must return without SQL when `profile.is_empty`. Convert each
blank field to `None` before parameters. The `ON CONFLICT` update must:

- preserve a stored field when the excluded field is `NULL`;
- fill a stored `NULL` field;
- replace a conflicting value only when excluded `observed_at` is known and is
  newer than or equal to stored `observed_at`;
- prevent a stale or untimestamped conflict from changing stored values;
- advance `observed_at` to the newest known timestamp;
- update `source_url` and `updated_at` only when the conflict `WHERE` clause
  accepts at least one field.

- [ ] **Step 4: Add a missing skip-empty test only if absent**

The exact behavioral assertion is:

```python
repository.save_enriched_user(
    make_enriched(profile=ProfileData())
)

assert not any(
    "INSERT INTO facebook_user_profiles" in sql
    for sql, _params in cursor.commands
)
```

If the test does not exist, add it, confirm it fails only if production emits
profile SQL, then make the smallest production correction.

- [ ] **Step 5: Re-run repository and pipeline unit tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline -q
```

Expected: all data-pipeline unit tests pass.

---

## Task 5: Apply migration 002 to PostgreSQL and verify end-to-end behavior

**Files:**

- Modify: `tests/integration/data_pipeline/test_postgres_repository.py`
- Modify: `docs/postgresql.md`
- Modify: `README.md` only if required for consistency.

**Interfaces:**

- Consumes: packaged migrations and `PostgresRepository` from prior tasks.
- Produces: verified PostgreSQL storage and documented query behavior.

- [ ] **Step 1: Confirm live tests skip without explicit configuration**

```powershell
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/data_pipeline/test_postgres_repository.py -q
```

Expected: live tests skip with `TEST_DATABASE_URL is not configured`.

- [ ] **Step 2: Run live PostgreSQL profile tests**

Use only the existing dedicated database ending in `_test`:

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/data_pipeline/test_postgres_repository.py -q
```

Expected tests prove:

- migration 002 applies idempotently;
- newer address replaces older address;
- blank birth date and gender preserve stored values;
- stale values cannot overwrite any of the three fields;
- the view returns `address`, `birth_date`, and `gender`;
- deleting a Facebook user cascades to its profile snapshot.

- [ ] **Step 3: Apply migration to the development database**

```powershell
$env:DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline"
.\.venv\Scripts\fb-crawl.exe pipeline migrate
.\.venv\Scripts\fb-crawl.exe pipeline migrate
```

Expected: the first run applies `002_profile_attributes` if it is not already
applied; the second run prints `applied=none`.

- [ ] **Step 4: Ensure documentation names all three raw fields**

`docs/postgresql.md` must state:

- `facebook_user_profiles` stores the current snapshot;
- `address`, `birth_date`, and `gender` are raw display text;
- `birth_date` is not coerced to SQL `date`;
- blank and stale snapshots do not replace stored values;
- the read view exposes all three fields.

- [ ] **Step 5: Run complete verification**

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\fb-crawl.exe pipeline --help
git diff --check
git status --short
```

Expected: all tests pass, live PostgreSQL tests execute rather than skip,
compilation and dependency checks exit zero, CLI help succeeds, and all changes
remain uncommitted in the working tree.

---

## Acceptance Checklist

- [ ] `ProfileData` contains trimmed raw `address`, `birth_date`, and `gender`.
- [ ] User and page importers preserve the fields available from crawler data.
- [ ] Duplicate bundle merging is non-destructive and timestamp-aware.
- [ ] FBNumber enrichment cannot drop or alter the profile snapshot.
- [ ] Migration 002 is packaged without modifying migration 001.
- [ ] PostgreSQL stores one current profile snapshot per Facebook user.
- [ ] Empty, stale, or untimestamped conflicting values cannot overwrite newer data.
- [ ] Profile writes share the atomic user transaction.
- [ ] The read view exposes all three fields together with `phone_1` and `phone_2`.
- [ ] The full suite passes against PostgreSQL 17.
- [ ] No commit, merge, push, cleanup, or artifact deletion occurs.
