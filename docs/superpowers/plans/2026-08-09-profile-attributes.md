# Profile Attributes Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve raw Facebook-visible `address`, `birth_date`, and `gender` values from crawler records through enrichment and into PostgreSQL.

**Architecture:** Introduce a typed `ProfileData` snapshot on `UserBundle`, populate and merge it inside `fb_data_pipeline`, then persist one current snapshot in a new `facebook_user_profiles` table created by migration `002`. The existing phone view is replaced to expose the three fields without changing phone selection rules.

**Tech Stack:** Python 3.12, dataclasses, psycopg 3, PostgreSQL 17, pytest.

## Global Constraints

- Work only in `D:/project/fb/fb-crawl`.
- Store `address`, `birth_date`, and `gender` as trimmed raw text.
- Do not parse `birth_date` into a date.
- Do not place profile attributes in `FacebookIdentity`.
- Do not modify `001_initial.sql`; add `002_profile_attributes.sql`.
- Empty or stale values never erase newer non-empty stored values.
- Preserve `phone_1` and `phone_2` selection behavior.
- Provider HTTP remains outside database transactions.
- Do not delete output artifacts.
- Do not commit, merge, push, or create a PR.
- Follow red-green-refactor for every production behavior.

---

### Task 1: Carry typed profile data through import, merge, and enrichment

**Files:**

- Modify: `src/fb_data_pipeline/core/models.py`
- Modify: `src/fb_data_pipeline/importers/crawler.py`
- Modify: `src/fb_data_pipeline/services/merge.py`
- Modify: `src/fb_data_pipeline/services/pipeline.py`
- Modify: `tests/unit/data_pipeline/test_models.py`
- Modify: `tests/unit/data_pipeline/test_crawler_importer.py`
- Modify: `tests/unit/data_pipeline/test_merge.py`
- Modify: `tests/unit/data_pipeline/test_pipeline.py`

**Interfaces:**

- Produces: `ProfileData(address, birth_date, gender, source_url, observed_at)`.
- Produces: `UserBundle.profile: ProfileData` with an empty default.
- Consumes: existing `UserRecord`, `PageRecord`, and `EnrichmentPipeline`.

- [ ] **Step 1: Write failing model and importer tests**

Add tests proving string cleaning, empty detection, the empty bundle default,
all three `UserRecord` fields, page address, source URL, and parsed UTC
observation timestamp.

```python
def test_profile_data_cleans_raw_values_and_detects_content() -> None:
    profile = ProfileData(
        address="  Ha Noi  ",
        birth_date="  12 thang 8, 1990  ",
        gender="  Nam  ",
        source_url="  https://www.facebook.com/a.user/about  ",
    )

    assert profile.address == "Ha Noi"
    assert profile.birth_date == "12 thang 8, 1990"
    assert profile.gender == "Nam"
    assert profile.is_empty is False
    assert UserBundle(FacebookIdentity(uid="100")).profile.is_empty is True
```

```python
def test_user_record_maps_profile_attributes() -> None:
    record = UserRecord(
        user_id="100",
        profile_url="https://www.facebook.com/a.user",
        source="profile",
        source_url="https://www.facebook.com/a.user/about",
        address="Ha Noi",
        birth_date="12 thang 8, 1990",
        gender="Nam",
        last_enriched_at="2026-08-09T01:02:03Z",
    )

    bundle, invalid = import_user_record(record)

    assert invalid == 0
    assert bundle.profile.address == "Ha Noi"
    assert bundle.profile.birth_date == "12 thang 8, 1990"
    assert bundle.profile.gender == "Nam"
    assert bundle.profile.source_url.endswith("/about")
    assert bundle.profile.observed_at == datetime(
        2026, 8, 9, 1, 2, 3, tzinfo=UTC
    )
```

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_models.py tests/unit/data_pipeline/test_crawler_importer.py -q
```

Expected: import or attribute failures because `ProfileData` and
`UserBundle.profile` do not exist.

- [ ] **Step 3: Implement the typed model and importer mapping**

Add this immutable model and default:

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

Add `profile: ProfileData = field(default_factory=ProfileData)` after evidence
on `UserBundle`. Populate it in both crawler import functions. Add
`_first_timestamp(*values)` so an invalid first timestamp does not hide a valid
later timestamp.

- [ ] **Step 4: Write failing merge and pipeline-preservation tests**

Test these exact rules:

- newer timestamp replaces a conflicting field;
- blank incoming fields preserve existing values;
- timestamped values beat conflicting untimestamped values;
- first non-empty value wins when both timestamps are absent;
- `EnrichmentPipeline` preserves crawler profile data after FBNumber merge.

- [ ] **Step 5: Run merge/pipeline tests and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_merge.py tests/unit/data_pipeline/test_pipeline.py -q
```

Expected: profile assertions fail because merge and enrichment currently drop
the snapshot.

- [ ] **Step 6: Implement profile merging and pipeline preservation**

Add `merge_profiles(left: ProfileData, right: ProfileData) -> ProfileData`.
Select each field with a helper implementing the five spec rules. Preserve the
newest known observation time and use the source URL of the newest accepted
snapshot. Pass `profile=merge_profiles(existing.profile, combined.profile)` in
`merge_bundles`, and `profile=original.profile` in `EnrichmentPipeline._run`.

- [ ] **Step 7: Verify the full model/import/merge slice**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_models.py tests/unit/data_pipeline/test_crawler_importer.py tests/unit/data_pipeline/test_merge.py tests/unit/data_pipeline/test_pipeline.py -q
```

Expected: all focused tests pass. Do not commit.

---

### Task 2: Add migration 002 and extend the preferred-slot view

**Files:**

- Create: `src/fb_data_pipeline/migrations/002_profile_attributes.sql`
- Modify: `tests/unit/data_pipeline/test_migrations.py`

**Interfaces:**

- Consumes: migration discovery from `load_migrations()`.
- Produces: `facebook_user_profiles` and view columns `address`, `birth_date`,
  `gender`.

- [ ] **Step 1: Write the failing migration contract test**

Require versions in immutable order:

```python
assert [item.version for item in load_migrations()] == [
    "001_initial",
    "002_profile_attributes",
]
```

Assert migration 002 creates the profile table and replaces the view without
modifying the stored checksum/content of migration 001.

- [ ] **Step 2: Run the migration test and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_migrations.py -q
```

Expected: only `001_initial` is discovered.

- [ ] **Step 3: Create migration 002**

Create `facebook_user_profiles` with a bigint primary/foreign key,
`address`, `birth_date`, `gender`, `source_url`, `observed_at`, audit times, and
a non-empty-field check. Use `ON DELETE CASCADE`.

Finish the migration with `CREATE OR REPLACE VIEW
facebook_user_phone_slots AS` using the same phone ranking CTE as migration
001, plus a left join to `facebook_user_profiles` and these selected columns:

```sql
profiles.address,
profiles.birth_date,
profiles.gender
```

- [ ] **Step 4: Run migration and wheel packaging tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_migrations.py -q
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir runtime\wheel-check
```

Expected: both migrations load in order and both SQL files are packaged. Do
not commit.

---

### Task 3: Upsert profile snapshots in the existing user transaction

**Files:**

- Modify: `src/fb_data_pipeline/repositories/postgres.py`
- Modify: `tests/unit/data_pipeline/test_postgres_repository.py`

**Interfaces:**

- Consumes: `EnrichedUser.bundle.profile`.
- Produces: `_upsert_profile(cursor, user_id, profile) -> None`.

- [ ] **Step 1: Write failing fake-repository tests**

Prove:

- non-empty profile SQL executes after user upsert and before phone SQL;
- parameters contain raw trimmed values, source URL, and observation time;
- empty profile skips the profile table entirely;
- a profile SQL failure rolls back the full current-user transaction.

- [ ] **Step 2: Run focused repository tests and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: no `facebook_user_profiles` command is recorded.

- [ ] **Step 3: Implement profile upsert**

Call `_upsert_profile` immediately after `_upsert_user`. Skip when
`profile.is_empty`. Convert blank strings to `None` and use a single
parameterized `INSERT ... ON CONFLICT (facebook_user_id) DO UPDATE`.

For each field, accept the incoming non-null value only when the stored field
is null, or the incoming timestamp is known and is not older than the stored
timestamp. Preserve stored fields otherwise. Advance `observed_at` to the
newest known value and update `source_url` only when the incoming snapshot is
accepted. Never interpolate profile values into SQL.

- [ ] **Step 4: Verify repository tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/data_pipeline/test_postgres_repository.py -q
```

Expected: all repository tests pass. Do not commit.

---

### Task 4: Verify PostgreSQL behavior and document the fields

**Files:**

- Modify: `tests/integration/data_pipeline/test_postgres_repository.py`
- Modify: `docs/postgresql.md`
- Modify: `README.md`

**Interfaces:**

- Consumes: dedicated `TEST_DATABASE_URL` ending in `_test`.
- Produces: verified view output and operational documentation.

- [ ] **Step 1: Write live PostgreSQL tests**

Add tests proving:

- migration 002 applies once and subsequent migration runs are empty;
- the view returns all three raw attributes;
- a newer partial snapshot updates its supplied field and preserves blank
  fields;
- an older conflicting snapshot cannot replace newer stored values;
- deleting a Facebook user cascades to its profile row.

- [ ] **Step 2: Run against PostgreSQL 17**

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
.\.venv\Scripts\python.exe -m pytest tests/integration/data_pipeline/test_postgres_repository.py -q
```

Expected: all live tests pass.

- [ ] **Step 3: Update documentation**

Document `facebook_user_profiles`, raw-text semantics, non-destructive update
rules, and view columns in `docs/postgresql.md`. Mention the three fields in
the PostgreSQL section of `README.md`.

- [ ] **Step 4: Apply migration to the development database**

```powershell
$env:DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline"
.\.venv\Scripts\fb-crawl.exe pipeline migrate
.\.venv\Scripts\fb-crawl.exe pipeline migrate
```

Expected: first run reports `applied=002_profile_attributes`; second reports
`applied=none`.

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

Expected: the complete suite and every static/runtime check pass. Preserve all
working-tree changes without committing.
