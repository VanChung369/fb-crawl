# Authenticated Persistence Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing authenticated in-memory FBNumber/PostgreSQL persistence flow to profile, engagement, and batch commands.

**Architecture:** Reuse the existing persistence flags, runtime, ingestion service, reports, output policy, and exit codes. Add a typed CLI boundary that unwraps only `AuthenticatedBatchResult.user_result`; profile and engagement results pass through unchanged.

**Tech Stack:** Python 3.12+, argparse, dataclasses, httpx, psycopg 3, PostgreSQL 17, pytest.

## Global Constraints

- Do not change PostgreSQL schema or migrations.
- Do not persist message or inspect records as Facebook users.
- Do not create compatibility output for `--persist` unless `--keep-output` is present.
- Preserve all legacy behavior when `--persist` is absent.
- Do not delete cache, session, checkpoint, target, or existing output files.
- Preserve existing provider/database failure policy and exit precedence.
- Do not commit, merge, push, or create a worktree.

---

### Task 1: Parser coverage for remaining user actions

**Files:**
- Modify: `src/fb_crawl/cli/authenticated.py`
- Modify: `tests/unit/cli/test_authenticated_parser.py`

**Interfaces:**
- Consumes: `_persistence_options(parser)` and existing authenticated subparsers.
- Produces: `args.persist` and `args.keep_output` on profile, engagement, and batch only; messages, inspect, and repair continue to reject the flags.

- [ ] **Step 1: Write failing parser tests**

Extend the supported action table with:

```python
("profile", ["https://www.facebook.com/synthetic.user"]),
("engagement", ["https://www.facebook.com/acme/posts/1"]),
("batch", ["--input", "runtime/targets.txt"]),
```

Remove those actions from the rejection table while keeping messages, inspect,
and repair rejected.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/cli/test_authenticated_parser.py -q
```

Expected: three failures because the new subparsers reject `--persist`.

- [ ] **Step 3: Add scoped parser options**

Call `_persistence_options(profile)`, `_persistence_options(engagement)`, and
`_persistence_options(batch)` immediately after their `_common(...)` setup.
Do not add the flags to messages, inspect, or repair.

- [ ] **Step 4: Verify GREEN**

Run the Task 1 command. Expected: all parser tests pass.

### Task 2: Typed result routing and CLI execution

**Files:**
- Modify: `src/fb_crawl/cli/authenticated.py`
- Modify: `tests/integration/test_authenticated_cli.py`

**Interfaces:**
- Consumes: `AuthenticatedAction`, `ScrapeResult[UserRecord]`, `AuthenticatedBatchResult`, and `AuthenticatedPersistenceRuntime.ingest_result`.
- Produces: `_pipeline_user_result(action, result) -> ScrapeResult[UserRecord]`.

- [ ] **Step 1: Write failing routing tests**

Add direct tests proving:

```python
assert _pipeline_user_result(AuthenticatedAction.PROFILE, regular) is regular
assert _pipeline_user_result(AuthenticatedAction.ENGAGEMENT, regular) is regular
assert _pipeline_user_result(AuthenticatedAction.BATCH, batch) is batch.user_result
```

Use a mixed `AuthenticatedBatchResult` whose message and inspect results contain
non-user records so the test proves they are not routed.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/integration/test_authenticated_cli.py -k "pipeline_user_result" -q
```

Expected: import failure because `_pipeline_user_result` does not exist.

- [ ] **Step 3: Implement the typed routing helper**

Import `AuthenticatedBatchResult` for runtime checking/typing and add:

```python
def _pipeline_user_result(action, result):
    if action is AuthenticatedAction.BATCH:
        if not isinstance(result, AuthenticatedBatchResult):
            raise TypeError("Batch action returned an unsupported result.")
        return result.user_result
    return result
```

The error contains no target, session, provider, or database details.

- [ ] **Step 4: Route ingestion through the helper**

Replace the current persistence call with:

```python
pipeline_result = _pipeline_user_result(action, result)
ingestion_report = persistence_runtime.ingest_result(pipeline_result)
```

Keep compatibility export before this call.

- [ ] **Step 5: Write failing CLI behavior tests**

Parameterize profile and engagement to assert their exact result object reaches
the persistence fake. Add batch tests asserting:

- the exact `user_result` object is ingested;
- default `--persist` does not call the batch exporter;
- `--keep-output` calls the exporter before ingestion;
- message/inspect-only batch routes an empty `user_result` and reports
  `pipeline_users=0`, `persisted=0`.

- [ ] **Step 6: Verify and complete GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/cli/test_authenticated_parser.py `
  tests/integration/test_authenticated_cli.py -q
```

Expected: all parser and authenticated CLI tests pass.

### Task 3: Documentation and complete verification

**Files:**
- Modify: `README.md`
- Modify: `docs/authenticated-cli.md`
- Modify: `docs/postgresql.md`

**Interfaces:**
- Consumes: final command contract.
- Produces: operator-facing list of every persistent authenticated user action and batch routing behavior.

- [ ] **Step 1: Update documentation**

List profile, engagement, and batch alongside the existing actions. Include one
profile example and one mixed batch explanation. State that only batch
`user_result` reaches FBNumber/PostgreSQL and that messages/inspect remain
compatibility output only.

- [ ] **Step 2: Run focused verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests/unit/cli/test_authenticated_parser.py `
  tests/integration/test_authenticated_cli.py -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run live PostgreSQL and full verification**

```powershell
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\fb-crawl.exe authenticated profile --help
.\.venv\Scripts\fb-crawl.exe authenticated batch --help
git diff --check
git status --short
```

Expected: full suite green, persistence flags visible for profile/engagement/
batch, flags absent for messages/inspect/repair, and only intentional
uncommitted files remain.
