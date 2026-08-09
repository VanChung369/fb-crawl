# fb-crawl

`fb-crawl` provides explicit public HTTP and authenticated browser modes behind reusable service boundaries. Public mode never reads a browser session; authenticated mode starts only when selected explicitly.

## Requirements

- Python 3.12+
- Access only to data you are authorized to collect

## Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

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
