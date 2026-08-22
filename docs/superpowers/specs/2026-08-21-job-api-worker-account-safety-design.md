# Job API, Background Worker, and Account Safety Design

**Date:** 2026-08-21  
**Status:** Approved in chat; awaiting written-spec review

## Context

Authenticated crawling currently runs synchronously inside the CLI process. The
CLI owns request parsing, browser creation, session validation, collection,
FBNumber enrichment, PostgreSQL persistence, summary output, and resource
cleanup. This is suitable for a local command but not for a WebUI: an HTTP
request must not remain open while Selenium scrolls Facebook, and a browser
failure must not erase job progress.

PostgreSQL is already the source of truth for Facebook identities, profile
attributes, phone evidence, and FBNumber enrichment attempts. This phase adds a
durable job boundary around that flow. FastAPI accepts and validates work, a
separate worker claims it from PostgreSQL, and the WebUI can later consume the
same API without owning a browser or session.

The operator has already received a Facebook account warning. Account safety is
therefore a functional requirement, not an optional performance setting. This
design reduces request frequency, bounds every run, and stops automatically on
risk signals. It does not attempt to hide automation or bypass Facebook
controls.

## Goals

- Store durable job, target, progress, event, and account-safety state in
  PostgreSQL.
- Run Selenium in a separate worker so API requests return immediately.
- Create, list, inspect, cancel, and retry crawl jobs through a versioned API.
- Expose per-target progress and sanitized errors through polling endpoints.
- Search and paginate persisted Facebook users and their enrichment evidence.
- Feed collected users through FBNumber and PostgreSQL without intermediate CSV
  files.
- Preserve partial progress and checkpoints when a job is cancelled, crashes,
  reaches a budget, or triggers the account circuit breaker.
- Enforce conservative, server-owned limits for one Facebook account/session.
- Keep the existing interactive CLI behavior compatible.

## Non-goals

- Building the WebUI itself.
- Running more than one Facebook account or authenticated browser concurrently.
- Distributed worker autoscaling, Redis, Celery, Kafka, or a general scheduler.
- Proxy rotation, fingerprint spoofing, CAPTCHA solving, stealth plugins,
  selector obfuscation, or any mechanism intended to evade detection.
- Bypassing login, two-factor authentication, checkpoints, privacy settings,
  rate limits, account restrictions, or other access controls.
- Automatically logging in with credentials supplied through the API.
- Persisting cookies, passwords, tokens, raw Facebook HTML, screenshots,
  FBNumber raw responses, or message contents in job events.
- Streaming with WebSockets or server-sent events; polling is sufficient for
  the first WebUI.
- Executing public-mode jobs in this phase. The schema retains a `mode` field,
  but public page records need a separate lossless PostgreSQL mapping for
  page-specific fields before they can use this source-of-truth flow.
- Exposing authenticated `messages`, `inspect`, `repair`, or CLI `batch` through
  the API. An API job already accepts multiple explicit targets, and the other
  actions either produce sensitive/non-user data or require local files.

## Architectural decision

Use PostgreSQL as both the durable job queue and the data source of truth:

```text
Future WebUI
    |
    v
FastAPI process ---- read/search ----> PostgreSQL user tables
    |
    +---- create/cancel/poll ---------> PostgreSQL job tables
                                            |
                                            v
                                     separate worker
                                            |
                               Selenium authenticated service
                                            |
                              FBNumber -> PostgreSQL persistence
```

The API process never creates a browser. The worker claims a job in a short
transaction using `FOR UPDATE SKIP LOCKED`, commits the lease, and releases the
transaction before starting Selenium. Browser work and provider calls never run
while a queue lock is held.

This is preferred over FastAPI `BackgroundTasks`, which lose ownership and
recovery semantics when the API process restarts, and over Celery/Redis, which
would add another source of state before the project needs distributed workers.

## Supported phase-one jobs

The create API accepts `mode="authenticated"` and one of these user-producing
actions:

- `members`
- `comments`
- `profile`
- `friends`
- `followers`
- `reactions`
- `engagement`

Each job contains one action and one to 100 normalized Facebook URL targets.
All jobs persist results. `persist=false`, output paths, local input paths, raw
CLI arguments, proxy values, session paths, provider tokens, and Facebook
credentials are rejected from the request contract. The backend obtains its
session, database, FBNumber, and optional fixed proxy configuration only from
the process environment.

The worker runs one target at a time. After a target finishes collection, its
typed `ScrapeResult[UserRecord]` is immediately passed through the existing
FBNumber ingestion and PostgreSQL persistence flow. This limits data loss to
the currently active target and removes the need to create or delete CSV/JSON
artifacts.

## PostgreSQL schema

Migration `003_job_orchestration.sql` adds four tables. Existing user and phone
tables remain authoritative and unchanged.

### `crawl_jobs`

Core columns:

- `id uuid primary key`
- `mode text not null`
- `action text not null`
- `account_key text not null default 'default'`
- `status text not null`
- `request_options jsonb not null default '{}'`
- `idempotency_key text`
- `request_fingerprint text not null`
- `retry_of_job_id uuid null references crawl_jobs(id)`
- `priority smallint not null default 0`
- `attempt integer not null default 0`
- `worker_id text`
- `lease_expires_at timestamptz`
- `heartbeat_at timestamptz`
- `cancel_requested_at timestamptz`
- `started_at`, `finished_at`, `created_at`, `updated_at`
- `requested_targets`, `completed_targets`, `failed_targets`
- `discovered_users`, `persisted_users`, `provider_retries_required`
- `current_target_id uuid`
- `error_code text not null default ''`
- `error_message text not null default ''`

Allowed statuses are:

```text
queued -> running -> succeeded | partial | failed | cancelled | blocked
queued -> cancelled
running -> cancelling -> cancelled
```

`partial` means usable records were persisted but at least one target stopped
at a crawl budget or ended incomplete without an account-safety block.
`blocked` is reserved for account/session safety or an uncertain stale browser
run. `failed` represents ordinary terminal collection, provider orchestration,
or database failures. A job with a mix of successful and failed targets is
`partial`; a job with no successful target is `failed`.

A unique constraint on non-null `idempotency_key` prevents duplicate create
requests. `request_fingerprint` is the SHA-256 digest of the canonical validated
request and lets the API distinguish a safe replay from key reuse with a
different body. A unique non-null `retry_of_job_id` permits only one direct
retry child per terminal job, making repeated retry requests return the same
child; another retry is created from that child if it later ends incomplete. A
partial unique index permits only one active (`running` or
`cancelling`) authenticated job for `account_key='default'`.

A terminal job has no `pending` or `running` targets. When a job stops early,
untouched targets inherit the reason-oriented terminal state (`partial` for a
job budget, `cancelled` for cancellation, `blocked` for account safety, or
`failed` for an ordinary fatal failure) while retaining `attempt=0`. This makes
incomplete work visible and retryable without falsely reporting an attempt.

### `crawl_targets`

Core columns:

- `id uuid primary key`
- `job_id uuid not null references crawl_jobs(id) on delete cascade`
- `target_key text not null`
- `target_url text not null`
- `target_kind text not null`
- `position integer not null`
- `status text not null`
- `attempt integer not null default 0`
- `checkpoint_path text not null`
- `started_at`, `finished_at`, `created_at`, `updated_at`
- `steps_completed`, `items_discovered`, `users_persisted`
- `provider_retries_required`
- `error_code text not null default ''`
- `error_message text not null default ''`

Allowed statuses are `pending`, `running`, `succeeded`, `partial`, `failed`,
`skipped`, `cancelled`, and `blocked`. `(job_id, target_key)` is unique.
`target_key` is computed from the action and canonical URL, not from untrusted
raw input.

Checkpoint paths are generated by the server under the existing runtime
checkpoint root and are never accepted from the API. Checkpoints remain local
runtime files because the current checkpoint service is file-backed; the job
row is the durable index and PostgreSQL remains the source of truth for user
data. Moving checkpoint payloads to object storage or PostgreSQL is deferred.

### `crawl_job_events`

This is an append-only, monotonically ordered event stream:

- `id bigint generated always as identity primary key`
- `job_id uuid not null references crawl_jobs(id) on delete cascade`
- `target_id uuid null references crawl_targets(id) on delete cascade`
- `event_type text not null`
- `level text not null`
- `safe_message text not null default ''`
- `counters jsonb not null default '{}'`
- `created_at timestamptz not null default now()`

Initial event types are `job_created`, `job_claimed`, `browser_started`,
`target_started`, `target_progress`, `target_completed`, `provider_progress`,
`facebook_rate_limited`, `account_warning`, `cancel_requested`, `job_cancelled`,
`job_blocked`, and `job_completed`.

Events contain allow-listed numeric counters and safe codes/messages only. They
never contain cookies, credentials, API keys, arbitrary browser URLs with query
parameters, HTML, screenshots, message content, or provider response bodies.

### `crawler_account_state`

Phase one has exactly one row with `account_key='default'`:

- `status`: `ready`, `cooldown`, `blocked`, or `manual_review`
- `cooldown_until`
- `last_job_id`
- `last_started_at`, `last_finished_at`
- `rate_limit_count_24h`
- `last_rate_limit_at`
- `last_warning_code`, `last_warning_at`
- `block_reason`
- `acknowledged_at`
- `created_at`, `updated_at`

The account row is locked while the worker decides whether it may claim an
authenticated job. Queued jobs remain queued while the account is not ready.
The same claim transaction checks that no active job already owns the account.

After every normal terminal job, the worker sets `status='cooldown'` and
`cooldown_until` to at least sixty minutes after browser closure. A later claim
transaction may atomically move an expired cooldown to `ready` when no warning
requires manual review. A Facebook rate-limit signal extends the cooldown to at
least six hours. The 24-hour rate-limit counter resets to one when the previous
signal is older than 24 hours; otherwise it increments.

## Lease and crash recovery

The worker identifies itself with a random process instance ID. Claiming a job
atomically changes it from `queued` to `running`, increments `attempt`, records
the worker ID, and assigns a lease. A heartbeat extends the lease every ten
seconds while work is active.

On worker startup, stale leases are inspected:

- a stale job that never started a browser may be returned to `queued`;
- a stale authenticated job with a started/running target becomes `blocked`;
- the account becomes `manual_review` because the final browser/account state
  is unknown;
- no active authenticated target is blindly replayed.

Recovery emits a sanitized event and retains its checkpoint. Only an explicit
account acknowledgement and a new retry job can continue blocked work.

## Worker execution flow

For each claimed job, the worker:

1. Revalidates job state, account state, options, target URLs, and cancellation.
2. Creates one non-interactive Firefox session using backend configuration.
3. Validates the saved Facebook session; it never prompts for email/password.
4. Processes pending targets sequentially.
5. Emits progress from bounded load/scroll/enrichment operations.
6. Checks cancellation and account-safety signals between browser operations.
7. Persists each completed target through FBNumber and PostgreSQL.
8. Updates target/job counters in short transactions.
9. Saves the current checkpoint and closes the browser on every exit path.
10. Derives the terminal job status from target results and safety state.

The existing authenticated CLI remains usable. Shared browser/service
composition is extracted behind an application-level runner so the CLI and
worker use the same typed request and result contracts without the worker
calling argparse or parsing CLI output.

## Execution control contract

Long-running collectors receive a narrow cooperative control port:

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

The CLI uses a no-op implementation, preserving current behavior. The worker
implementation reads cancellation from PostgreSQL, writes allow-listed events,
and invokes the centralized browser safety detector. Collectors check the port
before browser startup, before each target, between scroll/load attempts,
before each profile-enrichment navigation, and before FBNumber/persistence.

Cancellation is cooperative. The API never kills Firefox or the worker process
directly. Once cancellation is observed, the worker stops scheduling browser
actions, saves the checkpoint, closes resources, marks the active target and job
`cancelled`, and emits a terminal event. Existing browser timeouts bound the
delay between a cancel request and observation.

A queued job has no browser resources to clean up, so cancellation marks its
job and targets `cancelled` atomically. Only a running job uses the observable
`cancelling` state while the worker performs cooperative cleanup.

A separate navigation pacer shared by all collectors enforces the minimum
interval between top-level `browser.get` calls. It is injected alongside the
execution-control port: API workers use the mandatory eight-second policy and
the existing interactive CLI uses a no-op pacer to preserve compatibility.

## Mandatory account-safety policy

Safe mode cannot be disabled for API-created authenticated jobs. Server limits
override or reject unsafe client options:

- one authenticated browser/job at a time;
- at least eight seconds between top-level Facebook navigations;
- maximum thirty minutes wall-clock time per job;
- at least sixty minutes between authenticated jobs;
- load/scroll `steps` default `10`, maximum `20`;
- relationship `depth` maximum `2`;
- profile enrichment limit default `20`, maximum `50`;
- at most one retry for a transient navigation failure;
- every target is bounded by time and steps;
- no API option can bypass cooldown or manual review;
- proxy configuration cannot change during a job.

If a normal crawl budget is exhausted, available records are persisted, the
checkpoint is retained, and the target/job becomes `partial`. This is not an
account warning.

The safety detector recognizes these categories from canonical redirect paths,
session state, and conservative page markers:

- login/session expired;
- checkpoint or two-factor challenge;
- CAPTCHA;
- temporary block or Facebook rate limit;
- account restriction;
- unusual-activity or account-recovery warning.

Response policy:

- one Facebook rate-limit signal stops the job and starts a six-hour cooldown;
- two Facebook rate-limit signals within 24 hours require manual review;
- CAPTCHA, checkpoint, unusual-activity, recovery, or restriction signals
  require manual review immediately;
- session expiry blocks the job and never triggers automatic login;
- an ordinary navigation failure receives at most one bounded retry;
- a worker/browser crash preserves the checkpoint and never blindly retries the
  active authenticated target.

On a circuit-breaker signal, the active target and job become `blocked`, queued
jobs are not claimed, the browser closes, incomplete data is not sent to
FBNumber, and only a sanitized signal code is persisted. There is no automatic
cooldown override.

Manual recovery uses:

```text
POST /api/v1/account/default/acknowledge
```

The body must contain `{"acknowledged": true}`. The endpoint records an audit
timestamp and changes `manual_review`/`blocked` to `cooldown` when a cooldown is
still active, otherwise to `ready`. It does not solve a checkpoint, update the
session, or verify Facebook. The next worker preflight validates the session and
re-blocks immediately if the signal remains.

## HTTP API

FastAPI and Uvicorn are installed through an optional `api` package extra. The
two long-running entry points are:

```powershell
fb-crawl api serve --host 127.0.0.1 --port 8000
fb-crawl worker run
```

### Job endpoints

```text
POST /api/v1/jobs
GET  /api/v1/jobs
GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs/{job_id}/targets
GET  /api/v1/jobs/{job_id}/events?after_id=123
POST /api/v1/jobs/{job_id}/cancel
POST /api/v1/jobs/{job_id}/retry
```

Create requires `X-API-Key` and `Idempotency-Key` headers and returns HTTP 202.
Its typed body is:

```json
{
  "mode": "authenticated",
  "action": "members",
  "targets": ["https://www.facebook.com/groups/123/members"],
  "options": {
    "steps": 10,
    "max_duration_seconds": 300,
    "navigation_delay_seconds": 8,
    "enrich_profiles": true,
    "profile_limit": 20
  }
}
```

The accepted option keys are explicitly allow-listed by action. Unknown keys
are rejected. All successful creates are persistence jobs; there is no output
format or output path. Reusing an idempotency key with the same canonical body
returns the original job, while reusing it with a different body returns HTTP
409.

Cancel is idempotent. For a queued job it sets `cancel_requested_at` and marks
the job/targets `cancelled` in one transaction. For a running job it sets the
timestamp and moves the job to `cancelling`; terminal jobs remain unchanged.
Retry never mutates or reopens the old job. It creates a new job with
`retry_of_job_id`, copying the canonical
targets whose terminal states are `partial`, `failed`, `cancelled`, or
`blocked`, including untouched targets that inherited the parent job's terminal
reason. Copied targets reuse their server-generated checkpoint paths when the
action, URL, and options still match. It returns HTTP 409 while the account is
not ready.

List, target, and event endpoints use opaque keyset cursors rather than offset
pagination. Events use the monotonic `after_id` cursor for simple WebUI polling.

### Account endpoints

```text
GET  /api/v1/account/default
POST /api/v1/account/default/acknowledge
```

The read response exposes state, cooldown, last safe warning code, and relevant
timestamps. It never exposes cookies, the session path, proxy credentials, or
page content.

### User endpoints

```text
GET /api/v1/users
GET /api/v1/users/{id}
GET /api/v1/users/{id}/phone-evidence
GET /api/v1/users/{id}/enrichment-attempts
```

`GET /users` accepts `q`, `uid`, `username`, `phone`, `phone_origin`,
`has_phone`, `limit`, and `cursor`. Exact indexed lookup is used for UID and
normalized phone. Username and display-name search uses normalized prefix
matching in this phase; broad full-text/fuzzy search is deferred. Limits default
to 20 and cannot exceed 100.

Results are ordered by `(updated_at DESC, id DESC)` and use an opaque cursor
containing those values. The response combines identity fields,
`phone_1` (FBNumber), `phone_2` (fb-crawl), address, birth date, gender,
profile URL, and update timestamps. Evidence and attempts remain separate
endpoints so the list response stays bounded.

### Health endpoints

```text
GET /health/live
GET /health/ready
```

Liveness confirms that the API process is responsive. Readiness verifies the
PostgreSQL connection and required migration version only. Neither endpoint
opens or validates a Facebook browser session.

## API security and input safety

- All `/api/v1` endpoints require an `FB_CRAWL_API_KEY` of at least 32
  characters, compared in constant time.
  If interactive OpenAPI docs are enabled, their HTML and schema endpoints use
  the same authentication dependency.
- The default bind address is `127.0.0.1`.
- CORS is disabled unless an explicit origin allow-list is configured.
- Facebook targets pass the existing canonical normalizer and an action-specific
  path policy before any row is created.
- Non-HTTP schemes, credentials in URLs, external hosts, localhost, private
  network targets, login/checkpoint paths, and unsupported Facebook surfaces are
  rejected.
- Error responses and events use stable safe codes/messages; unexpected
  exceptions are logged locally without being returned verbatim.
- State transitions use conditional updates so duplicate cancel/retry/claim
  requests cannot corrupt a job.
- An internet-facing deployment requires a TLS reverse proxy, its own request
  rate limiting, and network access controls; those deployment components are
  outside this phase.

Configuration continues to come from the real process environment. This phase
updates `.env.example` and documentation but does not silently introduce
automatic `.env` loading into library code. Operators may export variables in
PowerShell or use their process manager/IDE environment configuration.

## Progress and error semantics

Job progress is a durable summary, not an exact percentage prediction. It
includes target counts plus discovered/persisted users. Target progress adds
steps completed and current collection counters. Profile enrichment, UID
resolution, FBNumber, and database persistence emit phase counters where the
existing services expose them.

The API distinguishes:

- Facebook account rate limiting, which activates the account circuit breaker;
- FBNumber provider rate limiting, which persists the Facebook user and creates
  a normal durable provider retry requirement;
- ordinary target failures, which do not put the Facebook account into manual
  review;
- database failures, which prevent a target from being reported as succeeded.

Safe error codes are stable machine-readable values. Safe messages may include
the canonical target URL but not arbitrary query parameters or browser page
content.

## Repository boundaries

New code is divided by responsibility:

- `fb_data_pipeline.migrations`: job orchestration schema;
- `fb_data_pipeline.repositories`: transactional job, target, event, account,
  and user-query repositories;
- `fb_crawl.services.jobs`: state machine and job application service;
- `fb_crawl.services.worker`: claim loop and target orchestration;
- `fb_crawl.services.execution_control`: cancellation/progress port;
- `fb_crawl.adapters.browser.account_safety`: pure signal classification plus
  browser adapter;
- `fb_crawl.api`: FastAPI application, dependencies, request/response schemas,
  and routers;
- `fb_crawl.cli.api` and `fb_crawl.cli.worker`: thin process entry points.

Repository methods own SQL and transaction boundaries. API routers do not run
SQL, create browsers, or call providers. The worker does not parse argparse
namespaces. Browser collectors know only the narrow execution-control port, not
PostgreSQL or FastAPI.

## Testing strategy

All regular tests remain offline and use synthetic Facebook content.

### Unit tests

- job and target state transition tables;
- option bounds and action-specific request validation;
- URL canonicalization and rejection policy;
- account signal classification and circuit-breaker transitions;
- cancellation observation and no-op CLI execution control;
- event payload allow-list and secret redaction;
- cursor encoding/decoding and user filter normalization;
- terminal-status derivation from target outcomes.

### PostgreSQL integration tests

- migration installation and constraints;
- concurrent `FOR UPDATE SKIP LOCKED` claims select a job once;
- active authenticated-job uniqueness;
- heartbeat renewal and stale-lease recovery;
- idempotent create/cancel and retry lineage;
- account-row locking prevents claims during cooldown/manual review;
- user search, phone-origin filters, stable keyset pagination, and evidence
  joins against real PostgreSQL.

### API tests

FastAPI `TestClient` covers authentication, 202 create, idempotency conflicts,
validation, job/target/event pagination, cancellation, retry state guards,
account acknowledgement, user PII protection, and safe error responses.

### Worker integration tests

A fake crawler and fake safety detector exercise the complete path:

```text
API create -> PostgreSQL queue -> worker claim -> progress events
-> fake user result -> fake FBNumber -> PostgreSQL persistence -> terminal job
```

Separate scenarios cover graceful cancellation, crawl-budget partial results,
provider rate limits, database failure, session expiry, account warning, and a
stale lease. No automated test logs into Facebook or uses a real account.

### Manual smoke test

After all automated tests pass, an operator may run one visible, one-target,
minimum-budget job against an authorized account. The smoke test verifies API
return latency, worker progress, PostgreSQL persistence, graceful cancellation,
and browser closure. It is never part of CI and must stop immediately on any
Facebook warning.

## Delivery sequence

The implementation plan will split work into three reviewable slices:

1. Migration, repositories, state machine, cursor primitives, and integration
   tests.
2. Worker, shared authenticated runner, execution control, progress,
   cancellation, account-safety policy, and fake-worker integration tests.
3. FastAPI, user queries, CLI entry points, configuration/docs, API tests, and
   one documented manual smoke procedure.

WebUI implementation starts only after these slices pass their offline and
PostgreSQL test suites.

## Acceptance criteria

- Creating a valid job returns within normal API latency and never starts
  Selenium in the request process.
- Restarting the API does not lose queued or running job metadata.
- Only one authenticated job can actively use the default account.
- Every target, scroll loop, navigation retry, profile-enrichment loop, and job
  has an enforced upper bound.
- A cancel request causes checkpoint save, browser closure, and a durable
  `cancelled` terminal state without a hard process kill.
- Account-warning signals stop browser work, block the job/account, preserve the
  checkpoint, and require explicit manual acknowledgement.
- No API option bypasses cooldown, login, CAPTCHA, checkpoint, privacy, or other
  controls.
- Completed target results are enriched through FBNumber and persisted directly
  to PostgreSQL with no output artifact.
- FBNumber failures remain distinguishable from Facebook account-safety events.
- Job, target, and event endpoints expose bounded, cursor-paginated progress and
  sanitized errors.
- User endpoints return stable keyset pages including `phone_1`, `phone_2`,
  address, birth date, and gender while requiring API authentication.
- Existing CLI commands and persistence behavior remain compatible.
- Full offline tests and PostgreSQL integration tests pass without live
  Facebook access or tracked runtime secrets/artifacts.

## Alternatives considered

### FastAPI in-process background tasks

Rejected because browser ownership would be coupled to the API lifecycle,
restarts would make recovery ambiguous, and long Selenium work could degrade
WebUI responsiveness.

### Celery and Redis

Deferred because one account intentionally permits only one authenticated job.
PostgreSQL already provides durable locking, transactions, queryable progress,
and the project source of truth.

### Automatically retry account warnings after a delay

Rejected because a cooldown does not prove that a checkpoint, CAPTCHA,
restriction, or unusual-activity warning has cleared. Manual review is required
for ambiguous or repeated signals.

### Increase throughput with multiple browsers, proxies, or stealth settings

Rejected because it conflicts with the account-safety goal and would attempt to
evade platform controls rather than reduce risk.

### Include public jobs immediately

Deferred because public page-specific fields are not yet represented losslessly
in the PostgreSQL user schema. Adding them here would mix a separate data-model
decision into the worker/API safety phase.
