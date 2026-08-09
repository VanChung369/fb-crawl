# FBNumber Durable Retry Worker Design

## Purpose

Add a PostgreSQL-backed CLI worker that retries FBNumber enrichment after a
previous durable attempt ended in `failed` or `rate_limited`. The worker must
survive process restarts, avoid duplicate concurrent runs, respect a cooldown,
and reuse the existing provider, enrichment, and persistence boundaries.

PostgreSQL remains the source of truth. The worker reads retry eligibility from
`enrichment_attempts` and writes every new outcome through the existing
per-user persistence transaction.

## Confirmed product rules

- Retry only the latest FBNumber attempt for each Facebook user.
- A latest status of `failed` or `rate_limited` is retryable.
- A latest status of `found` or `not_found` is terminal and is not retried.
- A candidate must have a Facebook UID or username because FBNumber cannot
  search safely from a profile URL alone.
- The default durable cooldown is 24 hours.
- The default batch limit is 20 users.
- `--force` ignores the cooldown but does not make terminal statuses retryable.
- `--dry-run` selects and reports candidates without calling FBNumber or
  writing enrichment data.
- Only one durable FBNumber retry worker may run at a time for one database.
- FBNumber results remain `phone_1`; existing crawler evidence remains
  `phone_2`.
- Do not add a general job table in this slice. WebUI/API job orchestration
  remains future work.
- Do not commit, push, delete runtime artifacts, or change existing migrations.

## CLI contract

Add a `retry` subcommand beneath the existing pipeline mode:

```powershell
fb-crawl pipeline retry
fb-crawl pipeline retry --limit 20 --cooldown-hours 24
fb-crawl pipeline retry --dry-run
fb-crawl pipeline retry --force --limit 20
```

Arguments:

- `--limit`: positive number of users selected in one run; default `20`.
- `--cooldown-hours`: non-negative hours since the latest retryable attempt;
  default `24`.
- `--force`: bypasses the cooldown only.
- `--dry-run`: performs candidate selection only.

The command loads the existing pipeline settings and requires both
`DATABASE_URL` and the FBNumber configuration for an actual retry. A dry run
requires only `DATABASE_URL` because it never constructs or calls the provider.

The normal summary is one machine-readable line:

```text
selected=20 persisted=20 found=4 not_found=10 failed=5 rate_limited=1 retry_pending=6
```

For a dry run, all execution counters are zero and the line also includes
`dry_run=true`. If no candidate is eligible, `selected=0` and the command
returns successfully.

## Candidate selection

`PostgresRepository` exposes a read method that returns immutable retry
candidates containing the database user ID, normalized `FacebookIdentity`,
latest provider status, latest attempt timestamp, and latest error code.

Candidate selection uses one latest row per `(facebook_user_id, provider)`.
The effective rules are:

1. Restrict provider history to `provider = 'fbnumber'`.
2. Resolve the newest attempt using `checked_at DESC, id DESC`; the ID is the
   deterministic tie-breaker.
3. Keep only newest statuses `failed` and `rate_limited`.
4. Require a non-empty stored UID or username.
5. Unless forced, require `checked_at <= now - cooldown`.
6. Order candidates by `checked_at ASC, attempt_id ASC, facebook_user_id ASC`
   so the oldest eligible failures are retried first.
7. Apply the requested limit after filtering and ordering.

The query is read-only and completes before any provider HTTP request begins.
No database transaction or row lock is held during HTTP calls.

## Concurrency control

The command acquires one PostgreSQL session-level advisory lock with a stable,
application-owned key before selecting candidates. The same database session
holds that lock until the run finishes and releases it in `finally`/context
manager cleanup.

The advisory lock is non-blocking. If another retry worker already owns it, the
second command does not wait and does not call FBNumber. It prints a clear
`worker_busy=true` summary and exits successfully because no data operation
failed. PostgreSQL automatically releases the session lock if the process or
connection terminates unexpectedly.

The lock serializes this CLI worker without introducing a job queue. It does not
claim to coordinate unrelated applications that ignore the same advisory-lock
contract.

## Components and boundaries

### Core model

Add a typed `RetryCandidate` model. It represents selection state only and does
not contain provider behavior or database connections. It can convert its
identity to an empty-evidence `UserBundle` for the existing enrichment pipeline.

Add a typed `RetryReport` that contains candidate count, persistence count,
provider result counts, pending retry count, dry-run state, and worker-busy
state. CLI formatting consumes this report rather than reconstructing business
rules.

### PostgreSQL retry source

Extend `PostgresRepository` with two focused capabilities:

- acquire/release the durable retry worker advisory lock;
- list eligible FBNumber retry candidates.

Existing `save_enriched_user` behavior remains unchanged. The provider attempt
and any returned `phone_1` evidence are written atomically per user.

### Retry service

Add an `FBNumberRetryService` that depends on narrow repository, provider,
enrichment, and persistence interfaces. Its flow is:

1. acquire the non-blocking worker lock;
2. select eligible candidates;
3. return immediately for busy, dry-run, or empty selections;
4. convert candidates to `UserBundle` values;
5. call the existing `EnrichmentPipeline.run_bundles`;
6. persist the resulting `PipelineRun` with
   `PipelinePersistenceService.persist`;
7. release the worker lock in all cases;
8. return one `RetryReport`.

The provider stays responsible for its existing short immediate retries. This
service provides the later, restart-safe retry layer.

### CLI composition

`fb_crawl.cli.pipeline` validates CLI values, builds the concrete repository and
provider, invokes the retry service, closes the provider client, prints the
report, and maps the report to an exit code.

## Error handling and exit codes

- Exit `0` when no candidates exist, a dry run completes, all selected users
  reach `found`/`not_found`, or another worker owns the lock.
- Exit `1` when the completed run contains at least one new `failed` or
  `rate_limited` provider result. Those attempts are still persisted.
- Exit `2` through the existing safe error boundary for invalid configuration
  or invalid CLI values.
- Exit `5` for PostgreSQL connection/query errors or non-recoverable
  persistence errors.
- Per-user identity conflicts keep the existing persistence policy: record the
  failure in the report, continue other users, and produce exit `5` because the
  intended database write was incomplete.
- `KeyboardInterrupt` follows the existing application behavior and exits
  `130`; provider and advisory-lock resources are still closed.

Provider failure must never erase an existing Facebook user, profile snapshot,
or crawler phone evidence. A repeated provider failure creates a new attempt,
which becomes the latest status and starts a new cooldown.

## Testing strategy

### Unit tests

- Candidate model and report invariants.
- Latest-attempt query parameters and deterministic ordering.
- Cooldown filtering, `--force`, limit validation, and identity filtering.
- Worker busy, empty selection, and dry-run paths never call the provider.
- Provider results are counted as found, not found, failed, or rate limited.
- Advisory lock and provider resources release on success and exceptions.
- Parser defaults and invalid CLI values.
- CLI summary and exit-code mapping.

### PostgreSQL integration tests

- A newer terminal attempt suppresses an older retryable attempt.
- A newer retryable attempt is selected only after cooldown.
- Equal timestamps use attempt ID as the deterministic latest-row tie-breaker.
- Users without UID and username are excluded.
- A real advisory lock prevents a second worker from acquiring the contract.
- A successful retry stores `phone_1` and a new `found` attempt.
- A repeated failure stores a new failed attempt and remains eligible only after
  the next cooldown.

### Regression verification

- Existing authenticated `--persist` commands keep their current behavior.
- Existing migrations `001` and `002` remain immutable.
- The full test suite, compile check, dependency check, CLI help, and diff check
  pass.

## Out of scope

- Multiple concurrent retry workers.
- A generic queue, leases, scheduling daemon, WebUI, or HTTP API.
- Automatic retry of `not_found`.
- Retrying providers other than FBNumber.
- Changing the existing immediate retry/backoff behavior inside
  `FBNumberProvider`.
- Deleting exports, cache, session, checkpoint, or target files.
