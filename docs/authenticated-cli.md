# Authenticated CLI

Authenticated mode opens Firefox and uses only data visible to a Facebook account you are authorized to operate. It does not bypass login, access controls, CAPTCHA, checkpoints, two-factor authentication, or privacy settings.

## Install

```powershell
python -m pip install -e ".[browser,dev]"

# Add XLSX only when required:
python -m pip install -e ".[browser,xlsx,dev]"
```

Firefox must be installed. Selenium Manager resolves the compatible driver.

## First interactive session

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --no-headless
```

When no valid session exists, enter the email in the terminal and the password in the hidden prompt. Complete any checkpoint or two-factor step manually in the visible browser before the verification timeout.

Credentials are not persisted. Validated cookies are stored at `runtime/session.json` with restricted permissions.

## Reuse the session headlessly

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --headless --steps 5 --delay 3
fb-crawl authenticated comments https://www.facebook.com/PAGE/posts/POST_ID --headless
```

Headless mode never requests credentials. It exits with code `3` when the saved session is missing, expired, or redirected to a login, checkpoint, or two-step route.

## Optional profile enrichment

Members/comments pages usually expose only identity links. Phone numbers,
website, address, location, and birthday may appear only on a profile's visible
profile-information sections. Enrichment is therefore explicit and disabled by
default.

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID `
  --enrich-profiles `
  --profile-fields phone,address,current_city,hometown,birth_date `
  --profile-limit 20 `
  --profile-delay 3
```

When `--profile-fields` is omitted, all documented enrichment fields are
requested. Supported values are `phone`, `website`, `address`, `current_city`,
`hometown`, and `birth_date`. `birth_year` is derived from a visible valid
birthday/year value.

Each selected unique user is visited at most once after global deduplication.
The browser opens at most two normalized directory routes per profile:
`directory_personal_details` and `directory_links`.
For numeric `profile.php?id=...` identities it uses equivalent bounded `sk=`
routes, then switches to Facebook's canonical vanity URL when a redirect,
canonical link, or Open Graph URL exposes it. A failed profile preserves the
base user and later profiles continue; session loss stops the run. Users beyond
`--profile-limit` remain valid base records with empty enrichment fields.

Profile sections are rendered asynchronously. The browser waits a bounded time
for the requested section. A persistent Facebook loading spinner is reported as
`authenticated_navigation_failed` instead of being exported as a successful
empty enrichment. `directory_links` is skipped unless `website` is requested;
failure of the required personal-details section cannot be hidden by a loaded
links section.

`current_city` (for example, a visible "Lives in" value), `hometown`, and a
street/business `address` remain distinct. The crawler never guesses one from
another.

## Batch

Create a UTF-8 file with one supported URL per line. Blank lines and lines beginning with `#` are ignored.

```text
# Visible group members
https://www.facebook.com/groups/GROUP_ID

# Visible post comments
https://www.facebook.com/PAGE/posts/POST_ID
```

Run:

```powershell
fb-crawl authenticated batch --input runtime/targets.txt --headless --output runtime/output/batch.csv
```

Invalid targets and bounded navigation or parser failures become issue rows while other targets continue. Session loss stops the entire run.

## Options and environment

- `--steps` defaults to `5` and must be greater than zero.
- `--delay` defaults to `3.0` seconds and must be zero or greater.
- `--headless` and `--no-headless` override `FB_CRAWL_HEADLESS`.
- `FB_CRAWL_HEADLESS` accepts `1`, `true`, `yes`, `on`, `0`, `false`, `no`, and `off`, case-insensitively.
- `--proxy` overrides `FB_CRAWL_PROXY`.
- HTTP, HTTPS, SOCKS4, and SOCKS5 proxy URLs without embedded credentials are supported.
- `--session-path` overrides `FB_CRAWL_SESSION_PATH`; the default is `runtime/session.json`.
- `--browser-timeout` overrides `FB_CRAWL_BROWSER_TIMEOUT_SECONDS`; the default is `30` seconds.
- `--verification-timeout` overrides `FB_CRAWL_VERIFICATION_TIMEOUT_SECONDS`; the default is `300` seconds.
- `--format` accepts `csv`, `json`, `txt`, or `xlsx`; the default is `csv`.
- `--enrich-profiles` explicitly enables visible profile-directory collection.
- `--profile-fields` selects a comma-separated subset of enrichment fields.
- `--profile-limit` defaults to `20` and must be greater than zero.
- `--profile-delay` defaults to `3.0` seconds and must be zero or greater.

A repository-local session path must stay under `runtime/`. An absolute external path may be used for a managed secret mount.

## Output

Default output files are:

```text
runtime/output/members.csv
runtime/output/comments.csv
runtime/output/batch.csv
```

CSV and XLSX columns are:

```text
user_id,name,username,page_name,category,website,address,current_city,hometown,birth_date,birth_year,phone_numbers,phone_sources,profile_url,source,source_url,error_code,error_message
```

Authenticated member/comment records populate the common identity and source
fields. Page-specific and phone fields remain empty when they are not
available. Public output uses the exact same schema and populates page-specific
fields when found.

JSON contains `records`, `issues`, `stats`, and optional `enrichment` coverage.

TXT contains user IDs, non-empty enrichment fields, and target-error lines.

Existing output is preserved when both records and issues are empty. Every non-empty write uses a same-directory temporary file and atomic replacement.

## Exit codes

- `0`: completed without target issues.
- `1`: one or more targets failed; successful users were still exported.
- `2`: invalid target, configuration, or missing optional dependency.
- `3`: authenticated session, login, or manual verification unavailable.
- `4`: output could not be replaced safely.

## Security and troubleshooting

- Treat `runtime/session.json` as a bearer credential.
- Do not share, upload, commit, or attach the session file to an issue.
- Never paste passwords, cookies, full HTML, or proxy credentials into logs or bug reports.
- Missing Firefox: install Firefox, then rerun the command.
- Missing browser extra: run `python -m pip install -e ".[browser]"`.
- Missing XLSX extra: run `python -m pip install -e ".[xlsx]"`; the command will not fall back to another format.
- Invalid or expired session: rerun visibly with `--no-headless` to create a new validated session.
- Checkpoint or two-factor prompt: complete it manually in the visible browser; the tool never bypasses it.
- Empty output: increase `--steps` within a reasonable bound and confirm the account can see the requested members or comments in Firefox.
- Empty enrichment fields: confirm the field is visible on the profile's About
  pages to the same account. Missing/hidden fields are not errors and are never
  guessed.
- Slow enrichment: each unique profile can require two bounded page loads plus
  `--profile-delay`; reduce `--profile-limit` for a shorter run.
- Treat exported birthday and location values as personal data and apply an
  appropriate retention/deletion policy.
- Selector failure after a Facebook UI change: retain the safe error message and CLI version, but do not include page HTML or session data.
