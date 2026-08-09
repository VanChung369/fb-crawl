# Authenticated Persistence Orchestration Design

Date: 2026-08-09

## Purpose

Connect authenticated Facebook collection directly to the existing FBNumber
enrichment and PostgreSQL persistence pipeline. PostgreSQL remains the durable
source of truth. CSV and other exports are optional compatibility artifacts,
not an intermediate transport.

This phase covers the authenticated user-producing actions `members`,
`comments`, `friends`, `followers`, and `reactions`. Public crawling, profile,
engagement, batch, messages, inspect, repair, durable job scheduling, WebUI,
and API endpoints remain outside this phase.

## Confirmed command contract

Each supported authenticated action accepts:

```text
--persist       Enrich the in-memory crawl result and write it to PostgreSQL.
--keep-output   Also write the normal CLI output artifact; requires --persist.
```

Without `--persist`, existing CLI behavior is unchanged and the normal output
file is written. With `--persist`, no output file is created by default. With
both flags, the normal output is written to `--output` or the existing default
path.

If `--persist` is combined with an explicit `--output` but not
`--keep-output`, validation fails instead of silently ignoring the requested
path. `--keep-output` without `--persist` also fails validation.

Example:

```powershell
fb-crawl authenticated friends <URL> --persist --headless
fb-crawl authenticated friends <URL> --persist --keep-output `
  --output runtime/output/friends.csv --headless
```

Cache, session, target, and checkpoint files are never deleted by this flow.

## Component boundaries

The existing components retain their responsibilities:

- `fb_crawl` collects and optionally profile-enriches visible user records.
- `EnrichmentPipeline` imports `ScrapeResult[UserRecord]`, normalizes crawler
  phone evidence as `phone_2`, calls FBNumber outside database transactions,
  and merges provider evidence as `phone_1`.
- `PipelinePersistenceService` writes the enriched users through the repository
  and returns a structured batch report.
- `PostgresRepository` owns one short atomic transaction per user and converts
  driver/connection failures to safe `DatabaseError` exceptions.
- a small authenticated pipeline runtime composes settings, FBNumber provider,
  enrichment pipeline, persistence service, and PostgreSQL repository. This
  composition is injectable in CLI tests and reusable by future API/job code.

The CLI coordinates components but does not parse provider responses or issue
SQL directly.

## Runtime flow

```text
validate CLI and pipeline configuration
  -> validate Facebook request
  -> start browser and collect ScrapeResult[UserRecord]
  -> optionally write compatibility output (--keep-output only)
  -> import and normalize the in-memory result
  -> call FBNumber once per merged identity
  -> merge phone_1 and phone_2 evidence
  -> persist one atomic PostgreSQL transaction per user
  -> print crawl, enrichment, and persistence counts
  -> close FBNumber client and Firefox in finally blocks
```

Pipeline configuration is validated before Firefox is started. No provider HTTP
request is made while a PostgreSQL transaction or identity lock is open.

## Persistence failure policy

Persistence processes enriched users in stable input order.

- A `DatabaseIdentityConflict` rolls back only that user's transaction, records
  the failure in the in-memory report, and continues with remaining users.
- Any other `DatabaseError`, psycopg driver error, or connection failure stops
  the batch immediately. Already committed users remain valid and reprocessing
  is safe.
- Provider `not_found` is a successful completed lookup.
- Provider `failed` and `rate_limited` still persist the Facebook user,
  crawler evidence, profile fields, and safe enrichment-attempt status.

The persistence report contains `intended`, `persisted`, `db_failed`,
`provider_retries_required`, persisted database user IDs, and safe failed
identity aliases/codes. It never includes a DSN, token, SQL value, or raw
provider response.

## Exit codes and summaries

The authenticated summary retains existing crawl counts and adds:

```text
pipeline_users=<n>
persisted=<n>
db_failed=<n>
provider_found=<n>
provider_not_found=<n>
provider_failed=<n>
provider_retries_required=<n>
```

Exit code precedence is:

1. `130` for an interrupted Facebook crawl after any collected users are
   safely processed;
2. `5` for a database/configuration persistence failure or any isolated
   identity conflict;
3. `3` for Facebook session failure through the existing exception path;
4. `1` for Facebook crawl issues or provider `failed`/`rate_limited` statuses;
5. `0` when crawling and persistence complete, including provider
   `not_found`.

## Output safety

The implementation does not create and then delete a transient CSV. Data moves
from `ScrapeResult` to typed pipeline objects in memory. When `--keep-output`
is requested, the artifact is written immediately after collection and before
external enrichment/persistence. Therefore a later provider or database error
does not erase an explicitly requested recovery artifact.

Existing output files are never deleted by this feature. An empty crawl keeps
the exporter's existing non-destructive behavior.

## Testing

Implementation follows red-green-refactor and covers:

1. parser acceptance only on the five supported actions;
2. validation of flag combinations before browser/provider/database work;
3. no exporter call for `--persist` without `--keep-output`;
4. exporter call before pipeline persistence with `--keep-output`;
5. typed in-memory orchestration and deterministic summary fields;
6. `found`, `not_found`, `failed`, and `rate_limited` exit behavior;
7. isolated identity conflicts continue while connection failures stop;
8. provider and browser resources close on every success/failure path;
9. PostgreSQL integration verifies crawler/profile/provider data reaches the
   authoritative view;
10. the full existing test suite remains green.

## Deferred scope

- automatic persistence for public crawl results;
- authenticated `profile`, `engagement`, and `batch` actions;
- durable retry workers and cooldown scheduling;
- job tables, API endpoints, and WebUI;
- concurrent provider calls or database writes;
- deletion of pre-existing runtime artifacts.
