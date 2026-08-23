# Authenticated job API and worker

The phase-one API accepts bounded authenticated crawl jobs and returns
immediately. PostgreSQL is the source of truth for jobs, targets, progress,
events, account safety, users, phone evidence, and FBNumber attempts. The API
process never creates Selenium or opens Facebook. `fb-crawl worker run` is a
separate, non-interactive process that owns the browser and processes one
target at a time.

API jobs persist directly to PostgreSQL after FBNumber enrichment. They have no
output format/path option and create no CSV, JSON, or XLSX artifact.

## Install, configure, and start

Install the browser, API, and development extras, then start PostgreSQL and
apply the packaged migrations:

```powershell
python -m pip install -e ".[browser,api,dev]"
docker compose up -d postgres
$env:DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline"
fb-crawl pipeline migrate
```

`.env.example` is an example only. `fb-crawl` does not automatically load a
real `.env`; set secrets in each process environment or in the process
manager/IDE. The API key must contain at least 32 nonblank characters.

```powershell
$env:DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline"
$env:FB_CRAWL_API_KEY = "replace-with-at-least-32-random-characters"
$env:FB_CRAWL_API_CORS_ORIGINS = ""
$env:FB_CRAWL_API_DOCS = "false"
$env:FB_CRAWL_SESSION_PATH = "runtime/session.json"
$env:FB_CRAWL_HEADLESS = "false"
$env:FB_NUMBER_API_URL = "https://api.fbnumber.com/v1/phone/search"
$env:FB_NUMBER_API_TOKEN = "replace-with-secret"
```

Start the API and worker in separate terminals with the same required backend
configuration:

```powershell
fb-crawl api serve --host 127.0.0.1 --port 8000
```

```powershell
fb-crawl worker run
```

The worker requires an existing saved session at `FB_CRAWL_SESSION_PATH`. It
never asks for a Facebook email, password, two-factor code, or CAPTCHA answer.
Create and validate the session interactively with the existing authenticated
CLI before running the worker. Keeping `FB_CRAWL_HEADLESS=false` is recommended
for the first authorized smoke run so account warnings remain visible.

`GET /health/live` confirms only that the HTTP process is alive.
`GET /health/ready` confirms PostgreSQL access and migration
`003_job_orchestration`; neither endpoint opens Facebook.

## Create a members job

All `/api/v1` routes require `X-API-Key`. Creating a job also requires a
nonempty `Idempotency-Key` no longer than 128 characters. This PowerShell
request uses a supported members URL and the mandatory safe bounds:

```powershell
$baseUrl = "http://127.0.0.1:8000"
$apiHeaders = @{
    "X-API-Key" = $env:FB_CRAWL_API_KEY
    "Idempotency-Key" = "members-group-123-20260822-01"
}
$createBody = @{
    mode = "authenticated"
    action = "members"
    targets = @("https://www.facebook.com/groups/123/members")
    options = @{
        steps = 10
        max_duration_seconds = 300
        navigation_delay_seconds = 8
        max_retries = 1
        enrich_profiles = $true
        profile_fields = @("phone", "address", "birth_date", "gender")
        profile_limit = 20
    }
} | ConvertTo-Json -Depth 6

$job = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/v1/jobs" `
    -Headers $apiHeaders `
    -ContentType "application/json" `
    -Body $createBody
$job.id
```

Create returns HTTP 202. Repeating the same canonical body with the same
idempotency key returns the original job. Reusing that key with a different
body returns HTTP 409. Supported actions are `members`, `comments`, `profile`,
`friends`, `followers`, `reactions`, and `engagement`; unknown options and
unsupported/private-network/non-Facebook targets are rejected.

## Poll status, targets, and events

Use the returned job ID. Job/target lists are bounded and use opaque keyset
cursors. Events use their monotonic numeric ID as `after_id` for polling.

```powershell
$authHeaders = @{ "X-API-Key" = $env:FB_CRAWL_API_KEY }
$jobId = $job.id

$status = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/jobs/$jobId" `
    -Headers $authHeaders

$targets = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/jobs/$jobId/targets?limit=20" `
    -Headers $authHeaders

$events = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/jobs/$jobId/events?after_id=0&limit=100" `
    -Headers $authHeaders

$afterId = if ($events.items.Count) { $events.items[-1].id } else { 0 }
$newEvents = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/jobs/$jobId/events?after_id=$afterId&limit=100" `
    -Headers $authHeaders
```

Job states are `queued`, `running`, `cancelling`, `succeeded`, `partial`,
`failed`, `cancelled`, and `blocked`. Progress is a durable summary, not an
exact predicted percentage. Event counters are restricted to target, step,
discovered-user, persisted-user, and provider-retry counts; events never expose
cookies, API tokens, arbitrary browser content, proxy credentials, or session
paths.

## Cancel, retry, and inspect account state

Cancellation is cooperative and idempotent. A queued job is cancelled in the
database. A running job moves to `cancelling`; the worker stops scheduling
browser work, saves the checkpoint, closes browser/provider resources, and then
records the terminal state.

```powershell
$cancelled = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/v1/jobs/$jobId/cancel" `
    -Headers $authHeaders
```

Retry creates a child job only from retryable terminal targets. It never
reopens the old job and returns HTTP 409 while the crawler account is not
`ready`.

```powershell
$retry = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/v1/jobs/$jobId/retry" `
    -Headers $authHeaders
```

Inspect the one configured account and explicitly acknowledge a manual hold:

```powershell
$account = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/account/default" `
    -Headers $authHeaders

$ackBody = @{ acknowledged = $true } | ConvertTo-Json
$account = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/v1/account/default/acknowledge" `
    -Headers $authHeaders `
    -ContentType "application/json" `
    -Body $ackBody
```

Acknowledgement only records an audit timestamp and allows a later preflight
when cooldown permits. It does not solve or verify a Facebook warning, update
the saved session, pass a checkpoint/CAPTCHA/two-factor challenge, or prove the
account is safe. Resolve the issue manually in Facebook and replace/revalidate
the saved session first; the next worker preflight blocks again if the signal
remains.

## Search persisted users

User queries are bounded to 100 rows and return an opaque `next_cursor`.
Filters include `q`, `uid`, `username`, `phone`, `phone_origin`, `has_phone`,
`limit`, and `cursor`.

```powershell
$users = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/users?q=nguyen&has_phone=true&limit=20" `
    -Headers $authHeaders

$userId = $users.items[0].id
$user = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/users/$userId" `
    -Headers $authHeaders
$phoneEvidence = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/users/$userId/phone-evidence?limit=20" `
    -Headers $authHeaders
$attempts = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/api/v1/users/$userId/enrichment-attempts?limit=20" `
    -Headers $authHeaders
```

The user response includes UID, username, name, profile URL, `phone_1`
(FBNumber), `phone_2` (Facebook-visible crawl evidence), address, birth date,
gender, and timestamps. Evidence and provider attempts remain separately
paginated.

## Mandatory Facebook account safety

API clients cannot disable or weaken these server-owned limits:

- one authenticated browser/job at a time for the `default` account;
- at least 8 seconds between top-level Facebook navigations;
- at most 30 minutes wall-clock time per job;
- at least 60 minutes cooldown after every normal terminal job;
- `steps` defaults to 10 and is at most 20;
- relationship `depth` is at most 2;
- profile enrichment defaults to 20 and is at most 50 profiles;
- at most one retry for an ordinary transient navigation failure;
- every target is bounded by time and steps;
- proxy/session/provider settings come from the worker environment and cannot
  be changed by a job request;
- no request can bypass cooldown or manual review.

A normal budget limit preserves the available records and checkpoint and marks
the result `partial`; it is not an account warning. Server-owned checkpoints
are stored under
`runtime/checkpoints/jobs/<job-id>/<target-id>.json` and are retained across
cancellation, partial results, and recovery.

The account circuit breaker behaves conservatively:

- one Facebook temporary-block/rate-limit signal stops the job and starts a
  cooldown of at least 6 hours;
- two Facebook rate-limit signals within 24 hours require manual review;
- checkpoint, two-factor, CAPTCHA, account restriction, unusual activity, and
  account recovery signals require immediate manual review;
- an expired/missing Facebook session blocks the job and never triggers an
  automatic login;
- an uncertain worker/browser crash preserves the active checkpoint and
  requires manual review rather than blindly retrying.

On an account-safety signal, the active target/job becomes `blocked`, queued
jobs are not claimed, the browser closes, and incomplete Facebook data is not
sent to FBNumber.

Facebook and FBNumber rate limits are different. A Facebook rate limit opens
the account circuit breaker described above. An FBNumber provider rate limit
does not imply a Facebook warning: the already collected Facebook user/evidence
is persisted, a sanitized `rate_limited` provider attempt and retry requirement
are stored, and the durable pipeline retry command can process it later.

Stop the worker immediately if Facebook displays any warning. There is no
stealth, proxy rotation, CAPTCHA bypass, automatic recovery, or access-control
bypass in this phase.

## Operational security

- Bind locally by default; an internet-facing deployment needs TLS, external
  request rate limiting, and network access controls.
- CORS is off unless `FB_CRAWL_API_CORS_ORIGINS` contains exact origins;
  wildcards are rejected.
- Swagger/OpenAPI docs are off by default. If enabled with
  `FB_CRAWL_API_DOCS=true`, `/docs` and `/openapi.json` still require
  `X-API-Key`.
- Keep database, API, FBNumber, proxy, and Facebook session credentials out of
  source control and logs. `runtime/` and `.env` remain Git-ignored.
- Use only accounts and data you are authorized to access. The worker reads
  only what the configured account can already see and does not infer hidden
  fields.
