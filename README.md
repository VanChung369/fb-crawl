# fb-crawl

`fb-crawl` provides explicit public HTTP and authenticated browser modes behind reusable service boundaries. Public mode never reads a browser session; authenticated mode starts only when selected explicitly.

## Requirements

- Python 3.12+
- Access only to data you are authorized to collect

## Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[browser,dev]"
```

## Web Dashboard & REST API

Launch the web dashboard and REST API with a single command (supports zero-config development preview or production PostgreSQL connection):

### 1. Quick Launch (Standalone / Development Mode)

```powershell
python -m fb_crawl api serve --dev --port 8000
```
Open your browser at: **`http://localhost:8000/`** (or `http://localhost:8000/docs` for interactive Swagger API documentation).

### 2. Production Mode (PostgreSQL & Background Worker)

```powershell
# Terminal 1: Start API Server & Web Dashboard
python -m fb_crawl api serve --host 127.0.0.1 --port 8000

# Terminal 2: Start Background Crawl Worker (Default: 1 Worker)
python -m fb_crawl worker run

# Terminal 3: Process Lead Finder CSV/XLSX export jobs
python -m fb_crawl worker run --kind export

# Hoặc chạy nhiều Worker song song cùng lúc (Ví dụ: 3 Workers xử lý 3 Job đồng thời)
python -m fb_crawl worker run --concurrency 3
```

On Windows, the project-local launcher starts the missing API, crawl-worker, and export-worker
processes with the current virtual environment and leaves matching processes
alone:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-lead-finder.ps1
```

Use `-WhatIf` to print the commands without starting processes. Logs are kept
under `runtime/api.*.log`, `runtime/crawl-worker.*.log`, and
`runtime/export-worker.*.log`. The export worker
updates its PostgreSQL heartbeat on every poll; `GET /api/v1/worker-health`
reports safe bearer-authenticated liveness. New export requests return
`export_worker_unavailable` when no heartbeat has been seen for 15 seconds.
Artifacts are written below `LEAD_FINDER_EXPORT_DIR` (default
`runtime/lead-finder-exports`) and retain the existing 24-hour expiry.

### Lead Finder account setup

Profile friend collection requires migration `015_friend_sessions` and an API restart.
In the extension, open a profile's Friends tab, select **Profile friends** under
**Collect interactions**, start collection, then scroll the list manually. The
collector saves friend rows into Scan sessions and stops when leaving that source.

After upgrading the extension's lead status/notes feature, apply migration
`014_account_leads` using the normal migration command from `fb-crawl`, then restart the API:

```powershell
.\.venv\Scripts\python.exe -m fb_crawl pipeline migrate
```

Lead status and notes are stored per account/customer on the backend, independently
of lookup history and scan sessions. The extension creates CSV/XLSX files in the
browser from all pages matching the applied filters; these downloads do not create
server export jobs or require the export worker. Existing server export APIs remain
available for older clients. Save edited notes before exporting; unsaved drafts are
not included. If another device edits the same lead, refresh notes and explicitly
choose the saved version before retrying a conflicting change.

Production product routes require independent JWT, token-HMAC, and license-HMAC
secrets, an HTTPS `LEAD_FINDER_PUBLIC_BASE_URL`, SMTP settings, and
`LEAD_FINDER_TIMEZONE` (default `Asia/Ho_Chi_Minh`). Configure the versioned
license keyring as `LEAD_FINDER_LICENSE_HMAC_KEYS=1:<base64-32-byte-secret>` and
set `LEAD_FINDER_LICENSE_HMAC_ACTIVE_VERSION=1`. Keep older versions in the
comma-separated keyring while their licenses may still be redeemed. See
`.env.example` for all variable names. Never reuse `FB_CRAWL_API_KEY` as a
product secret and never place passwords or license keys in command arguments.

After PostgreSQL is available, create the first administrator once. The
password is prompted twice without echo and is never accepted as a CLI option:

```powershell
fb-crawl admin bootstrap --email admin@example.com
```

Account deletion revokes access immediately. Run the idempotent purge command
from a scheduler after the configured recovery window; it deletes only
account-owned rows and does not cascade into shared Facebook identity/evidence:

```powershell
fb-crawl admin purge-deleted-accounts
```

### Web Dashboard Features:
- 📊 **Dashboard Overview**: Realtime analytics with 6 metric cards, recent jobs, and latest discovered leads.
- ⚡ **Jobs Manager**: Create crawl jobs with full advanced controls (max users, scroll depth, deep profile enrichment, post phone scanning), live event streaming logs, cancel, and retry.
- 👥 **Leads Explorer**: Search and filter by Name, UID, Phone; inspect phone provenance and confidence evidence; download CSV/JSON exports.
- 🛡️ **Session Pool Manager**: Automated browser login and cookie extraction via Account/Password/2FA/Proxy, or manual JSON cookie import.
- 🌐 **Proxy Pool Manager**: Automated proxy rotation, health monitoring (Active/Cooldown/Dead), and bulk proxy imports.
- **Lead Finder Admin**: Secure product-account login, one-time license creation, masked key inventory, account suspension, session revocation, and subscription scheduling.


## Public commands

```powershell
fb-crawl public page https://www.facebook.com/example
fb-crawl public search --keyword "spa" --target pages --limit 20
fb-crawl public crawl https://www.facebook.com/example --depth 1 --max-nodes 20
fb-crawl public crawl https://www.facebook.com/groups/pythonvn --target all --depth 0
```

Use `--format json` for the full result envelope or `--output PATH` to select a destination. Default output is `runtime/output/pages.csv`.

Detailed options and output schemas are documented in [docs/public-cli.md](docs/public-cli.md).

## Authenticated commands

Install the optional browser dependencies and bootstrap a session visibly:

```powershell
python -m pip install -e ".[browser,dev]"
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --no-headless
```

Then reuse the validated session explicitly:

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --headless
fb-crawl authenticated comments https://www.facebook.com/PAGE/posts/POST_ID --headless
fb-crawl authenticated profile https://www.facebook.com/USERNAME --headless
fb-crawl authenticated friends https://www.facebook.com/USERNAME --headless
fb-crawl authenticated followers https://www.facebook.com/USERNAME --headless
fb-crawl authenticated reactions https://www.facebook.com/PAGE/posts/POST_ID --headless
fb-crawl authenticated engagement https://www.facebook.com/PAGE/posts/POST_ID --headless
fb-crawl authenticated messages https://www.facebook.com/messages/t/THREAD_ID --headless
fb-crawl authenticated inspect https://www.facebook.com/USERNAME --format json --headless
fb-crawl authenticated batch --input runtime/targets.txt --headless
fb-crawl authenticated repair runtime/output/friends.csv --headless
```

Authenticated user commands automatically resolve vanity links such as
`/USERNAME` to the account's numeric Facebook UID. The resolver only accepts a
UID paired with that username in the same profile-route object, so it does not
mistake the logged-in account's UID for the target. `user_id` contains only a
numeric UID in output; `username` remains a separate field.

By default, scrollable authenticated surfaces continue until Facebook stops
loading new visible content. Use `--max-duration` for a time budget or `--steps`
for a hard attempt limit. Friends and followers also support bounded BFS:

```powershell
fb-crawl authenticated friends https://www.facebook.com/USERNAME `
  --depth 2 --max-users 500 --max-duration 120 --headless
```

Every authenticated URL uses the same bounded retry policy. Transient
navigation, parsing, and rate-limit failures retry with exponential backoff and
jitter; session/checkpoint/2FA failures stop immediately. Override the defaults
with `--max-retries`, `--retry-backoff`, and `--retry-jitter`.

Resolved username-to-UID pairs are written atomically after each profile to
`runtime/cache/profile-uids.json` and reused by later runs. Add `--force` to
ignore cached mappings, resolve them again, and refresh the cache.

Optionally enrich a bounded number of unique profiles with fields visible to
the authenticated account:

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID `
  --enrich-profiles `
  --profile-fields phone,current_city,birth_date `
  --profile-limit 20 `
  --phone-post-steps 5
```

When `phone` is requested, the initially rendered profile intro and post text
are scanned automatically. `--phone-post-steps` and `--phone-post-duration`
optionally load more visible posts with a hard per-profile budget. Each found
number keeps its source URL, timestamp, and confidence in a sibling
`*-phone-evidence.csv` file.

Long runs can use an atomic runtime checkpoint:

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID `
  --resume --headless

fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID `
  --incremental --headless
```

`--resume` continues unfinished targets and returns the combined checkpointed
result. `--incremental` re-runs targets and emits only newly observed
identities.

Repair suspicious or legacy CSV identities without crawling the relationship
surface again:

```powershell
fb-crawl authenticated repair runtime/output/friends.csv `
  --output runtime/output/friends-repaired.csv --limit 20 `
  --max-retries 2 --retry-backoff 5 --retry-jitter 1 --headless
```

The repair pass preserves every existing CSV column, verifies only suspicious
rows, and records `identity_status` plus `identity_source`. Use
`--retry-failed` for previous repair failures or `--force` to verify every
supported profile row. Progress is written atomically before and after every
profile; `Ctrl+C` leaves a resumable `interrupted` row instead of discarding the
completed batch.

See [docs/authenticated-cli.md](docs/authenticated-cli.md) for supported URLs, session handling, formats, exit codes, and security guidance.

The external-enrichment direction is documented in
[docs/future-data-pipeline.md](docs/future-data-pipeline.md).

## PostgreSQL source of truth

The `fb_data_pipeline` package can normalize crawler records, call FBNumber,
preserve `phone_1` from FBNumber and `phone_2` from Facebook-visible evidence,
preserve raw `address`, `birth_date`, and `gender` profile values, and persist
the merged data to regular PostgreSQL 17.

Start PostgreSQL and apply the packaged migrations:

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline"
fb-crawl pipeline migrate
```

Set the FBNumber credential and persist supported authenticated user results
directly from memory:

```powershell
$env:FB_NUMBER_API_TOKEN = "replace-with-secret"
fb-crawl authenticated friends https://www.facebook.com/example `
  --persist --headless
```

`profile`, `members`, `comments`, `friends`, `followers`, `reactions`,
`engagement`, and `batch` support `--persist`. This mode does not create a CSV
by default. Add `--keep-output` to also write the normal compatibility
artifact. For mixed batches, only `user_result` reaches FBNumber/PostgreSQL;
message and inspect results remain compatibility output only. Cache, session,
checkpoint, and existing output files are never deleted. See
[docs/postgresql.md](docs/postgresql.md) for schema, tests, and security.

Retry durable FBNumber failures from PostgreSQL without crawling Facebook
again:

```powershell
fb-crawl pipeline retry --limit 20 --cooldown-hours 24
fb-crawl pipeline retry --dry-run
fb-crawl pipeline retry --force --limit 20
```

Only the latest `failed` or `rate_limited` FBNumber attempt is eligible.
`found` and `not_found` are terminal. The default worker is bounded to 20 users,
waits 24 hours after a retryable failure, and serializes runs per database.
`--force` bypasses only the cooldown; `--dry-run` calls neither FBNumber nor the
persistence writer.

## Job API and background worker

The authenticated job API stores queue, target, event, account-safety, and user
state in PostgreSQL. The API process never starts Selenium; a separate
non-interactive worker uses the configured saved session and processes one
target at a time. API jobs persist directly to PostgreSQL and do not create CSV
or JSON output artifacts.

Install all phase-one development extras, apply migration 003, and start the
two processes in separate terminals:

```powershell
python -m pip install -e ".[browser,api,dev]"
$env:DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline"
$env:FB_CRAWL_API_KEY = "replace-with-at-least-32-random-characters"
$env:FB_NUMBER_API_TOKEN = "replace-with-secret"
$env:FB_CRAWL_SESSION_PATH = "runtime/session.json"
fb-crawl pipeline migrate
fb-crawl api serve --host 127.0.0.1 --port 8000
fb-crawl worker run
```

`.env.example` is documentation only. The application does not load `.env`
automatically; export real secrets as process variables or configure them in a
process manager/IDE. See [docs/job-api.md](docs/job-api.md) for a complete
members request, polling, cancellation, account acknowledgement, user search,
fixed account-safety limits, and recovery behavior.

## Merge crawl output

Combine unified public/authenticated user CSV files and produce a quality gate
before future external enrichment or PostgreSQL storage:

```powershell
fb-crawl data merge runtime/output/*.csv `
  --output runtime/output/users-master.csv `
  --report runtime/output/quality-report.json
```

Rows are linked by numeric UID, normalized profile URL, or username; names are
never identity keys. Conflicting values remain visible in the report. See
[docs/data-merge.md](docs/data-merge.md) for merge and coverage rules.

Generate a bounded follow-up batch only for incomplete or suspicious profiles:

```powershell
fb-crawl data plan runtime/output/users-master.csv `
  --missing phone,address,current_city,birth_year `
  --failure-cooldown-days 1 `
  --output runtime/targets/enrichment.txt
```

The plan respects `last_enriched_at` with a 30-day default cooldown and writes
typed targets accepted directly by `authenticated batch`. Transient
`navigation_failed` and `section_unavailable` field statuses use a shorter
one-day retry cooldown, while `not_visible` keeps the normal cooldown. See
[docs/data-plan.md](docs/data-plan.md) for the complete crawl/merge/plan loop.

Merge the separate phone provenance files into an auditable master without
flattening them into user rows:

```powershell
fb-crawl data phone-merge runtime/output/*-phone-evidence.csv `
  --output runtime/output/phone-evidence-master.csv `
  --report runtime/output/phone-evidence-quality.json
```

Vietnamese `0`, `84`, `+84`, and `0084` prefix forms are normalized using the
configurable `--default-country-code`. See
[docs/phone-evidence.md](docs/phone-evidence.md) for schema and quality rules.

## Exit codes

- `0`: run completed without target failures
- `1`: partial target failure; successful records remain available
- `2`: invalid input or configuration
- `3`: authenticated session, login, or manual verification unavailable
- `4`: output could not be written safely
- `5`: database operation failed safely
- `130`: authenticated collection was stopped safely with `Ctrl+C`

## Privacy and safety

Generated data is written under Git-ignored `runtime/`. Public mode does not use cookies or login credentials. Authenticated collectors only read content visible to the authorized account; hidden profile fields and inaccessible lists are not inferred or bypassed. The project does not bypass access controls, CAPTCHA, checkpoints, or two-factor authentication. Message exports contain sensitive conversation text and should use an appropriate retention/deletion policy.

## Development checks

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
git diff --check
```
