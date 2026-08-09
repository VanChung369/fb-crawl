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

Resolved username-to-UID pairs are written atomically after each profile to
`runtime/cache/profile-uids.json` and reused by later runs. Add `--force` to
ignore cached mappings, resolve them again, and refresh the cache.

Optionally enrich a bounded number of unique profiles with fields visible to
the authenticated account:

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID `
  --enrich-profiles `
  --profile-fields phone,current_city,birth_date `
  --profile-limit 20
```

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

See [docs/authenticated-cli.md](docs/authenticated-cli.md) for supported URLs, session handling, formats, exit codes, and security guidance.

The future external-enrichment/PostgreSQL direction is documented in
[docs/future-data-pipeline.md](docs/future-data-pipeline.md). It is not active in
the current CLI.

## Exit codes

- `0`: run completed without target failures
- `1`: partial target failure; successful records remain available
- `2`: invalid input or configuration
- `3`: authenticated session, login, or manual verification unavailable
- `4`: output could not be written safely

## Privacy and safety

Generated data is written under Git-ignored `runtime/`. Public mode does not use cookies or login credentials. Authenticated collectors only read content visible to the authorized account; hidden profile fields and inaccessible lists are not inferred or bypassed. The project does not bypass access controls, CAPTCHA, checkpoints, or two-factor authentication. Message exports contain sensitive conversation text and should use an appropriate retention/deletion policy.

## Development checks

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
git diff --check
```
