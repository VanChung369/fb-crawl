# PostgreSQL source of truth

PostgreSQL 17 is the durable source of truth for normalized Facebook
identities, phone evidence, and FBNumber attempt history. CSV and JSON exports
remain compatibility artifacts and are not authoritative.

The implementation uses regular PostgreSQL and `psycopg`. It does not require
Supabase services, SDKs, authentication, or APIs.

## Start PostgreSQL locally

Install the project and start the repository Compose service:

```powershell
python -m pip install -e ".[dev,browser,xlsx]"
docker compose up -d postgres
docker compose ps postgres
```

Use the development values from `.env.example` only for local development:

```powershell
$env:DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline"
$env:DATABASE_STATEMENT_TIMEOUT_SECONDS = "5"
```

Do not commit a real database password or place the DSN in a CLI argument.

## Apply migrations

```powershell
fb-crawl pipeline migrate
```

The command prints `applied=001_initial` on the first run and `applied=none`
when the database is current. Applied migration checksums are stored in
`schema_migrations`; changing an already-applied SQL resource is rejected.

## Data model

- `facebook_users` stores normalized UID, username, display name, and canonical
  profile URL.
- `facebook_user_profiles` stores the current Facebook-visible `address`,
  `birth_date`, and `gender` snapshot plus its source URL and observation time.
- `phone_numbers` stores each normalized phone once.
- `user_phone_evidence` keeps every source, source URL, provider, correlation
  ID, confidence, capture range, and observation count.
- `enrichment_attempts` records safe `found`, `not_found`, `rate_limited`, and
  `failed` provider outcomes without request bodies, tokens, or raw responses.
- `facebook_user_phone_slots` is a read-only view over identity and evidence.

The preferred slots are derived rather than writable:

- `phone_1` is the newest FBNumber value.
- `phone_2` is the strongest Facebook crawl value, then the newest value.

Profile values remain trimmed raw display text. In particular, `birth_date` is
not forced into a SQL `date`, because Facebook may expose a localized full
date, a partial date, or only a year. Empty and stale incoming values do not
replace newer non-empty values.

Query the current preferred values with:

```sql
SELECT
    facebook_uid,
    facebook_username,
    display_name,
    profile_url,
    phone_1,
    phone_2,
    address,
    birth_date,
    gender
FROM facebook_user_phone_slots;
```

Provider failure does not discard the Facebook user or crawler evidence. The
failed attempt is stored so the durable retry worker can process it later.

## Retry durable FBNumber failures

Inspect eligible work without calling the provider or writing enrichment data:

```powershell
fb-crawl pipeline retry --dry-run
```

Run a bounded retry batch after the default 24-hour cooldown:

```powershell
$env:FB_NUMBER_API_TOKEN = "replace-with-secret"
fb-crawl pipeline retry --limit 20 --cooldown-hours 24
```

Use `--force` to ignore only the cooldown:

```powershell
fb-crawl pipeline retry --force --limit 20
```

Candidate selection considers only the latest `fbnumber` attempt per user.
Latest `failed` and `rate_limited` attempts are retryable; `found` and
`not_found` are terminal. A candidate must have a UID or username. The oldest
eligible failures run first, with attempt ID as the deterministic timestamp
tie-breaker.

One non-blocking PostgreSQL session advisory lock serializes this CLI worker per
database. A second worker reports `worker_busy=true` and exits without calling
FBNumber. The lock session uses autocommit, so provider HTTP calls do not run
inside a database transaction. A successful or failed retry writes a new
attempt and starts from that latest state on the next run.

Dry-run requires only `DATABASE_URL`. An actual retry also requires the normal
FBNumber configuration. Exit codes are `0` for completed, empty, dry, or busy
runs; `1` when a new provider result is still failed/rate-limited; `2` for
invalid CLI/configuration values; `5` for database failure; and `130` for an
operator interruption.

## Application flow

`EnrichmentPipeline` calls FBNumber before database persistence. Its
`PipelineRun` is then passed to `PipelinePersistenceService`, which writes one
user per short transaction through `PostgresRepository`. An identity conflict
rolls back only that user and the batch continues; a connection or driver
failure stops the run. Earlier committed users remain valid and a rerun is
idempotent except for evidence counters and attempt audit rows.

Authenticated `profile`, `members`, `comments`, `friends`, `followers`,
`reactions`, `engagement`, and `batch` commands opt into this flow with
`--persist`. The typed `ScrapeResult` is passed directly in memory; no
intermediate artifact is created or deleted. Batch unwraps only its typed
`user_result`; message and inspect records are not written as Facebook users.
Add `--keep-output` to also write the normal compatibility output. Existing
files, cache, session, targets, and checkpoints are retained.

```powershell
$env:FB_NUMBER_API_TOKEN = "replace-with-secret"
fb-crawl authenticated members https://www.facebook.com/groups/123 `
  --persist --headless
```

## Integration tests

Tests refuse to truncate a database unless its name ends in `_test`. Create a
dedicated database and run:

```powershell
docker compose exec postgres createdb -U fb_pipeline fb_pipeline_test
$env:TEST_DATABASE_URL = "postgresql://fb_pipeline:fb_pipeline_dev@localhost:5432/fb_pipeline_test"
python -m pytest tests/integration/data_pipeline/test_postgres_repository.py -q
```

If the test database already exists, skip the `createdb` command. Without
`TEST_DATABASE_URL`, the live PostgreSQL module skips safely.

## Production guidance

- Use separate non-superuser migration and application roles when practical.
- Grant the application only the required table and sequence privileges.
- Require TLS and rotate both database and provider credentials.
- Use a transaction-mode connection pooler such as PgBouncer.
- Keep transactions short and provider HTTP calls outside transactions.
- Back up PostgreSQL and regularly test restoration.
- Define retention and deletion rules before storing sensitive personal data.

Stop the local service without deleting its data volume:

```powershell
docker compose down
```
