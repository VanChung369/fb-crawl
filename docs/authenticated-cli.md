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
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID --headless --max-duration 120 --delay 3
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
`hometown`, `birth_date`, `bio`, `workplace`, `education`, `gender`,
`languages`, and `relationship_status`. `birth_year` is derived from a visible
valid birthday/year value.

Each selected unique user is visited at most once after global deduplication.
The browser opens only the normalized routes needed by the requested fields:
`directory_personal_details`, `directory_work`, and `directory_links`.
When `phone` is requested, it also scans the target profile's currently visible
intro and initially rendered post text for strong phone patterns. Those values
are tagged separately as `facebook:profile_intro_text` or
`facebook:post_text`; they never overwrite a dedicated profile-contact value.
Plain dates, counters, and ambiguous digit strings are rejected unless an
explicit phone/contact context is present.
For numeric `profile.php?id=...` identities it uses equivalent bounded `sk=`
routes, then switches to Facebook's canonical vanity URL when a redirect,
canonical link, or Open Graph URL exposes it. A failed profile preserves the
base user and later profiles continue; session loss stops the run. Users beyond
`--profile-limit` remain valid base records with empty enrichment fields.

Profile sections are rendered asynchronously. The browser waits a bounded time
for the requested section. The parser supports both older semantic lists and
the current value/label rows such as `Current city`, `Birthday`, `Mobile`,
`Address`, and `Website`. `directory_links` is skipped unless `website` is
requested; failure of the required personal-details section cannot be hidden by
a loaded links section.

On the Links section, external websites may be rendered as visible domain text
without an `href`. The parser accepts that shape only on `directory_links` and
continues to reject Facebook-owned domains.

`current_city` (for example, a visible "Lives in" value), `hometown`, and a
street/business `address` remain distinct. The crawler never guesses one from
another.

Flat outputs include `field_status`, `field_sources`, `first_seen`, `last_seen`,
and `last_enriched_at`. Field status values are `found`, `not_visible`,
`section_unavailable`, `navigation_failed`, and `not_requested`. This separates
a genuinely absent visible value from a route/render failure. Sources remain
additive so a future external provider can append evidence without overwriting
Facebook-visible evidence.

Timeline phone scanning is bounded to content already rendered for the target
profile. It does not infer hidden values, open inaccessible posts, or attach a
post author's number to commenters and reaction users.

## Direct profiles and visible social lists

The `profile` command accepts vanity and numeric profile URLs and automatically
enables profile enrichment. It does not require the profile to be discovered
from a group or comment first:

```powershell
fb-crawl authenticated profile https://www.facebook.com/USERNAME `
  --profile-fields phone,website,address,current_city,hometown,birth_date `
  --headless
```

Friends and followers use the same normalized profile forms. Only rows rendered
for the authenticated account are collected; an empty or privacy-restricted
list remains empty and is never reconstructed from other sources.

When a rendered user link contains only `/USERNAME`, the CLI visits that visible
profile and resolves the numeric UID from a username-linked route object before
export. A standalone `profile_id` or `userID` is never trusted because it may
belong to the logged-in account or another user rendered on the page. Resolution
uses `--profile-delay` between profiles. If a UID cannot be verified, `user_id`
is left empty, `username` is preserved, and an
`authenticated_uid_resolution_failed` issue is exported.

Each verified `username -> UID` pair is saved immediately and atomically in
`runtime/cache/profile-uids.json`. A later run reuses the cache without opening
that profile again. The cache contains identifiers only, never cookies, HTML,
passwords, or profile enrichment fields.

Use `--force` when mappings must be verified again. It bypasses cache reads for
vanity usernames, performs fresh UID resolution, and replaces the corresponding
cache entries. Profiles already represented by numeric `profile.php?id=...`
links do not need another resolution request.

```powershell
fb-crawl authenticated friends https://www.facebook.com/USERNAME --steps 10 --headless
fb-crawl authenticated followers https://www.facebook.com/profile.php?id=USER_ID --steps 10 --headless
```

Relationship traversal uses breadth-first search. The requested profile is
depth `0`, directly visible users are depth `1`, and their visible relationships
are depth `2`:

```powershell
fb-crawl authenticated friends https://www.facebook.com/USERNAME `
  --depth 2 --max-users 500 --max-duration 120 --headless
```

`--max-users` is a global output bound across all BFS levels. Only a user with a
verified numeric UID can be used as the next traversal node.

Use `--enrich-profiles` with either list when the discovered users should also
receive bounded profile enrichment. `--profile-limit` still controls the number
of profiles enriched; it does not cap the identity-resolution pass required to
produce numeric UIDs for all exported users.

## Repair identities in an existing CSV

`repair` reads an existing user CSV and verifies only suspicious profile rows.
It is useful for old exports containing a missing UID or username, a mismatched
profile URL, or a social-context label such as `174 friends` in the `name`
column.

```powershell
fb-crawl authenticated repair runtime/output/friends.csv `
  --output runtime/output/friends-repaired.csv `
  --limit 20 --delay 3 `
  --max-retries 2 --retry-backoff 5 --retry-jitter 1 --headless
```

The input file is never overwritten by default. The default destination is a
sibling named `INPUT-repaired.csv`. Every original column and value is
preserved unless one of `user_id`, `name`, `username`, or `profile_url` is
verified and corrected.

Repair provenance uses these columns:

- `identity_status=collected`: exported by a normal crawl but not independently
  verified from the profile.
- `identity_status=verified`: the profile confirmed the existing identity.
- `identity_status=repaired`: at least one identity value was corrected.
- `identity_status=failed`: the bounded verification attempt failed.
- `identity_status=running`: the atomic checkpoint was written immediately
  before opening this profile.
- `identity_status=interrupted`: processing stopped safely before this row
  completed; it is selected automatically on the next run.
- `identity_source=facebook:profile`: identity was checked on the target
  profile.
- `identity_error_code` and `identity_error_message`: safe failure details that
  do not replace the original crawl error columns.

`--limit` defaults to `20`. When the summary reports `pending` greater than
zero, use the repaired output as the next input to process the next batch.
Successfully verified/repaired rows are skipped automatically. Add
`--retry-failed` to retry failed rows, or `--force` to verify all supported
profile rows again. A blank username remains valid when Facebook exposes only a
numeric `profile.php?id=...` identity.

The output CSV is also the per-profile checkpoint. It is written atomically
before and after every profile, so completed rows survive `Ctrl+C`, browser
navigation failures, and session loss. Use the repaired output as the next
input; `verified` and `repaired` rows are skipped while `running` and
`interrupted` rows resume automatically.

Transient navigation, blank-profile parsing, and rate-limit surfaces use a
finite retry policy. `--max-retries` defaults to `2`; `--retry-backoff`
defaults to `5` seconds and grows exponentially up to 300 seconds;
`--retry-jitter` defaults to `1` second. Session/checkpoint failures are never
retried blindly. The summary reports `retried`, `rate_limited`,
`session_failed`, `interrupted`, and `pending` separately.

## Visible post reactions

`reactions` accepts the same supported post, video, reel, photo, and permalink
forms as `comments`. It opens the visible reactions dialog without adding or
changing a reaction, loads it until exhaustion or a configured limit, and
exports the visible users.

```powershell
fb-crawl authenticated reactions https://www.facebook.com/PAGE/posts/POST_ID `
  --steps 10 --headless
```

The command returns a target issue when Facebook does not expose an actionable
reactions dialog to the current account.

## Combined post engagement

`engagement` performs the comments and reactions passes for each supported post
and globally deduplicates the resulting users:

```powershell
fb-crawl authenticated engagement https://www.facebook.com/PAGE/posts/POST_ID `
  --steps 10 --enrich-profiles --headless
```

User rows expose `commented`, `reacted`, `reaction_types`, and
`interaction_count`. A reaction type is populated only when Facebook renders a
clear accessible label for that user row; unknown types remain empty.

## Visible conversation messages

`messages` requires one or more explicit conversation URLs. The inbox root is
rejected: the CLI never enumerates every conversation implicitly. It scrolls
upward a bounded number of times and exports visible text, sender information,
and timestamps when those values are present in the rendered page.

```powershell
fb-crawl authenticated messages `
  https://www.facebook.com/messages/t/THREAD_ID `
  --steps 10 --output runtime/output/messages.csv --headless
```

Message output uses its own schema:

```text
message_id,sender_name,sender_profile_url,text,sent_at,thread_url,source,first_seen,last_seen,error_code,error_message
```

Message output also includes `first_seen` and `last_seen` timestamps.

When Facebook does not expose a stable message ID in the rendered DOM, the CLI
creates a deterministic capture ID from the visible row. It is a local export
identifier, not a Facebook API identifier. Attachments without visible text are
represented only when Facebook provides an accessible visible label. Profile
enrichment options are not accepted by `messages`.

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

Batch also accepts explicit action prefixes:

```text
members:https://www.facebook.com/groups/GROUP_ID
comments:https://www.facebook.com/PAGE/posts/POST_ID
profile:https://www.facebook.com/USERNAME
friends:https://www.facebook.com/USERNAME
followers:https://www.facebook.com/USERNAME
reactions:https://www.facebook.com/PAGE/posts/POST_ID
engagement:https://www.facebook.com/PAGE/posts/POST_ID
messages:https://www.facebook.com/messages/t/THREAD_ID
inspect:https://www.facebook.com/USERNAME
```

Untyped legacy group/post lines keep their previous behavior. A mixed batch
writes user rows to the requested output and message/diagnostic rows to sibling
files such as `batch-messages.csv` and `batch-inspect.csv`.

## Resume and incremental collection

All authenticated commands accept mutually exclusive `--resume` and
`--incremental` modes. The default checkpoint is
`runtime/checkpoints/ACTION.json`; override it with `--checkpoint`.

```powershell
fb-crawl authenticated members GROUP_URL --resume --headless
fb-crawl authenticated members GROUP_URL --incremental --headless
```

Checkpoints are atomic JSON and contain normalized records, known identities,
completed targets, and safe issues. They never contain cookie/session content,
passwords, full HTML, or screenshots. Resume is target-level because Facebook
does not provide a stable private infinite-scroll cursor. A checkpoint is
rejected if its action or normalized target set differs from the command.
Each completed target is persisted immediately. `Ctrl+C` returns the completed
targets, records an interrupted target, and exits with code `130`; the next
matching `--resume` run skips completed targets and continues from that target.

## Safe diagnostics

Use `inspect` after a Facebook UI change or unexpected empty output:

```powershell
fb-crawl authenticated inspect PROFILE_OR_TARGET_URL `
  --format json --output runtime/output/inspect.json --headless
```

It returns only booleans/counts for document readiness, main/dialog presence,
visible profile links, message rows, profile-field labels, session validity, and
parser version. It never writes DOM text, raw HTML, screenshots, or cookies.

## Options and environment

- `--steps` is an optional positive hard limit on load/scroll attempts.
- `--max-duration` is an optional positive time limit in seconds per scrollable
  surface.
- With neither `--steps` nor `--max-duration`, collection continues until the
  surface stops growing or no visible load-more action remains. `Ctrl+C` remains
  an explicit manual stop signal.
- `--delay` defaults to `3.0` seconds and must be zero or greater.
- `--max-retries` defaults to `2` for each authenticated target.
- `--retry-backoff` defaults to `5.0` seconds and grows exponentially, capped
  at 300 seconds.
- `--retry-jitter` defaults to `1.0` second and randomizes retry timing.
- Navigation, parse, UID-resolution, profile-enrichment, and rate-limit issues
  are retryable. Invalid input and session/checkpoint/2FA failures are not.
- `--depth` defaults to `1` for friends/followers and must be greater than zero.
- `--max-users` defaults to `1000` and bounds unique BFS user output.
- `--force` ignores cached username-to-UID mappings, resolves them again, and
  refreshes the cache.
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
- `--profile-delay` defaults to `3.0` seconds, must be zero or greater, and also
  spaces automatic numeric-UID resolution requests.
- `--resume` continues a matching checkpoint and skips completed targets.
- `--incremental` emits only identities not already known by the checkpoint.
- `--checkpoint` overrides the default JSON path and requires one of the two
  checkpoint modes.

A repository-local session path must stay under `runtime/`. An absolute external path may be used for a managed secret mount.

## Output

Default output files are:

```text
runtime/output/members.csv
runtime/output/comments.csv
runtime/output/profile.csv
runtime/output/friends.csv
runtime/output/followers.csv
runtime/output/reactions.csv
runtime/output/engagement.csv
runtime/output/messages.csv
runtime/output/inspect.csv
runtime/output/batch.csv
```

CSV and XLSX columns are:

```text
user_id,name,username,page_name,category,website,address,current_city,hometown,birth_date,birth_year,bio,workplace,education,gender,languages,relationship_status,phone_numbers,phone_sources,field_status,field_sources,first_seen,last_seen,last_enriched_at,commented,reacted,reaction_types,interaction_count,profile_url,source,source_url,depth,identity_status,identity_source,identity_error_code,identity_error_message,error_code,error_message
```

Authenticated profile/member/comment/friend/follower/reaction records populate
the common identity and source fields. Page-specific and phone fields remain
empty when they are not available. Public output uses the exact same schema and
populates page-specific fields when found. Messages use the separate
conversation schema documented above.

For authenticated users, `user_id` is numeric only. `username` stores the vanity
handle independently. A failed UID verification never writes the username into
the UID column.

JSON contains `records`, `issues`, `stats`, optional `enrichment`, optional
`uid_resolution`, and target-level `retry` coverage. CLI summaries distinguish
UID coverage plus `targets_attempted`, `retried`, `rate_limited`, `pending`, and
`interrupted` counts.

TXT contains user IDs, usernames, non-empty enrichment fields, and target-error
lines.

Existing output is preserved when both records and issues are empty. Every non-empty write uses a same-directory temporary file and atomic replacement.

## Exit codes

- `0`: completed without target issues.
- `1`: one or more targets failed; successful users were still exported.
- `2`: invalid target, configuration, or missing optional dependency.
- `3`: authenticated session, login, or manual verification unavailable.
- `4`: output could not be replaced safely.
- `130`: authenticated collection was interrupted safely; completed target
  progress was preserved.

## Security and troubleshooting

- Treat `runtime/session.json` as a bearer credential.
- Do not share, upload, commit, or attach the session file to an issue.
- Never paste passwords, cookies, full HTML, or proxy credentials into logs or bug reports.
- Missing Firefox: install Firefox, then rerun the command.
- Missing browser extra: run `python -m pip install -e ".[browser]"`.
- Missing XLSX extra: run `python -m pip install -e ".[xlsx]"`; the command will not fall back to another format.
- Invalid or expired session: rerun visibly with `--no-headless` to create a new validated session.
- Checkpoint or two-factor prompt: complete it manually in the visible browser; the tool never bypasses it.
- Empty output: confirm the account can see the requested list, dialog, or
  conversation in Firefox; when using a limit, increase `--steps` or
  `--max-duration` within a reasonable bound.
- Slow UID resolution: vanity links require one visible profile navigation each;
  keep `--profile-delay` nonzero for long lists. Later runs reuse the UID cache.
- Empty enrichment fields: confirm the field is visible on the profile's About
  pages to the same account. Missing/hidden fields are not errors and are never
  guessed.
- Slow enrichment: each unique profile can require up to four bounded page loads plus
  `--profile-delay`; reduce `--profile-limit` for a shorter run.
- Treat exported phone, birthday, and location values as personal data and apply an
  appropriate retention/deletion policy.
- Treat message exports as highly sensitive. Keep them under `runtime/`, limit
  access, and delete them when they are no longer required.
- Hidden fields or privacy-restricted friends/followers/messages cannot be
  collected. The CLI records only what the same account can render manually.
- Selector failure after a Facebook UI change: retain the safe error message and CLI version, but do not include page HTML or session data.
