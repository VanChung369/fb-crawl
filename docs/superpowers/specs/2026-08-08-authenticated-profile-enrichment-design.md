# Authenticated Profile Enrichment Design

**Date:** 2026-08-08
**Status:** Approved for planning by user acknowledgement

## Context

The authenticated CLI currently collects identity links from group-member and
post-comment pages. It parses the HTML already loaded for those targets and
returns `UserRecord` values containing an ID, display name, profile URL, and
source information. It does not navigate to each discovered profile.

That boundary explains the current coverage gap: phone numbers, websites,
locations, and birthday information may be visible on a profile's About pages
but are absent from the members/comments HTML. The supplied DOM sample confirms
that a visible profile can expose a personal-information section containing a
current city and a full birthday while exposing no phone entry in the same
fragment.

The project should stabilize this enrichment behavior and its output contract
before adding an HTTP API or WebUI. Otherwise those consumers would be built on
an incomplete schema and would need immediate contract migrations.

## Terminology

- **Public mode:** unauthenticated HTTP collection implemented by the existing
  `public` commands.
- **Authenticated mode:** Selenium collection through a validated Facebook
  session. This is the codebase term for what an operator may informally call
  private mode.
- **Base record:** a user identity collected from members or comments.
- **Profile enrichment:** optional navigation to a base record's visible About
  routes to collect additional fields.
- **Address:** a street/business address explicitly shown as an address.
- **Current city:** a value shown as "Lives in" or "Sống ở". It is not silently
  promoted to a street address.
- **Hometown:** a value shown as "From" or "Đến từ".

## Goals

- Add explicit, opt-in enrichment to authenticated members, comments, and batch
  commands.
- Visit only supported Facebook profile About routes through the already
  validated browser session.
- Collect visible phone numbers, website, address, current city, hometown,
  birthday, and derived birth year.
- Preserve field meaning: current city, hometown, and street address remain
  separate.
- Keep profile navigation sequential, delayed, bounded, and limited per run.
- Enrich each globally deduplicated user at most once.
- Continue after a profile-level navigation or parsing failure while treating
  session loss as fatal for the run.
- Use a pure, fixture-tested parser that ignores obfuscated Facebook CSS class
  names.
- Extend the unified public/authenticated output schema before API/WebUI work.
- Report enrichment coverage so operators can distinguish missing public data
  from navigation or parser failures.

## Non-goals

- Bypassing privacy settings, login, checkpoints, CAPTCHA, two-factor
  authentication, or other access controls.
- Collecting data that the authenticated operator cannot see manually.
- Guessing a phone number, address, city, hometown, birthday, or birth year.
- Deriving age or birthday from work/education dates.
- Treating "Lives in" as a full address.
- Friends, followers, messages, reactions, or private profile fields.
- Parallel browser pools, distributed workers, proxy rotation, stealth,
  anti-detection behavior, or selector auto-repair.
- Persisting raw Facebook HTML or adding a profile-data cache in this phase.
- WebUI, HTTP API, database, scheduler, or job queue implementation.
- Automated tests against live Facebook or a real account.

## Evidence from the supplied DOM

The supplied fragment is useful as structural evidence but must not be copied
into the repository because it contains real profile data. A synthetic fixture
will reproduce only these structural characteristics:

- a section heading linked to a `role="list"` through `aria-labelledby`;
- list items containing a "Lives in"/"Sống ở" value;
- a list item containing a localized full date;
- separate work and education sections containing unrelated years;
- an external website link;
- no stable semantic CSS class names.

For the supplied fragment, a compliant parser would identify a current city
and a birthday, derive the birth year from that birthday, and return no phone
number because the fragment contains no phone link or contact value.

## User-facing CLI contract

Existing authenticated commands retain their current behavior unless profile
enrichment is selected explicitly.

```text
fb-crawl authenticated members URL [URL ...] --enrich-profiles
fb-crawl authenticated comments URL [URL ...] --enrich-profiles
fb-crawl authenticated batch --input PATH --enrich-profiles
```

New common options:

```text
--enrich-profiles
--profile-fields phone,website,address,current_city,hometown,birth_date
--profile-limit N
--profile-delay SECONDS
```

Rules:

- Enrichment defaults to disabled.
- `--profile-fields` is accepted only with `--enrich-profiles`.
- When enrichment is enabled and fields are omitted, all documented profile
  fields are requested.
- `--profile-limit` defaults to `20` and must be greater than zero.
- The limit applies after global user deduplication and across the whole run.
- `--profile-delay` defaults to `3.0` seconds and must be zero or greater.
- Users beyond the limit remain valid base records with empty enrichment fields.
- Headless mode never prompts for credentials and keeps the existing session
  behavior.

Examples:

```powershell
fb-crawl authenticated members https://www.facebook.com/groups/GROUP_ID `
  --enrich-profiles `
  --profile-fields phone,current_city,birth_date `
  --profile-limit 20 `
  --profile-delay 3

fb-crawl authenticated comments https://www.facebook.com/PAGE/posts/POST_ID `
  --enrich-profiles `
  --headless
```

## Core contracts

### Profile fields

```python
class ProfileField(StrEnum):
    PHONE = "phone"
    WEBSITE = "website"
    ADDRESS = "address"
    CURRENT_CITY = "current_city"
    HOMETOWN = "hometown"
    BIRTH_DATE = "birth_date"
```

`birth_year` is always derived from an accepted birthday/year value and is not
selected independently.

### Request options

`ScrapeRequest` gains immutable enrichment settings:

```python
enrich_profiles: bool = False
profile_fields: tuple[ProfileField, ...] = ()
profile_limit: int = 20
profile_delay_seconds: float = 3.0
```

The model validates the positive limit, non-negative delay, unique fields, and
the rule that fields require enrichment.

### Profile details

```python
@dataclass(frozen=True, slots=True)
class ProfileDetails:
    phone_numbers: tuple[str, ...] = ()
    phone_sources: tuple[str, ...] = ()
    website: str | None = None
    address: str | None = None
    current_city: str | None = None
    hometown: str | None = None
    birth_date: str | None = None
    birth_year: int | None = None
```

`birth_date` uses ISO `YYYY-MM-DD` only when a complete date can be parsed. If a
visible value supplies only a plausible year, `birth_date` remains `None` and
`birth_year` may contain that year. Dates outside a conservative human range are
ignored rather than guessed.

### Enriched user records

`UserRecord` gains the same optional profile fields. Existing constructors
remain valid through defaults. Tuple fields are deduplicated in discovery order;
scalar merge behavior keeps the first non-empty value.

### Enrichment statistics

`ScrapeResult` gains optional `EnrichmentStats`:

```python
@dataclass(frozen=True, slots=True)
class EnrichmentStats:
    selected: int
    attempted: int
    succeeded: int
    failed: int
    phone_found: int
    address_found: int
    current_city_found: int
    hometown_found: int
    birth_year_found: int
```

No enrichment run uses `None`; a requested run reports zeros where appropriate.
Public results and authenticated runs without enrichment retain `None`.

## Unified output schema

The tabular/normalized output order becomes:

```text
user_id,name,username,page_name,category,website,address,current_city,hometown,birth_date,birth_year,phone_numbers,phone_sources,profile_url,source,source_url,error_code,error_message
```

Mapping rules:

- Authenticated records populate enrichment fields only when the profile parser
  observes them.
- Public page records continue to populate page-specific fields; profile-only
  fields remain empty unless the public parser has equivalent explicit data.
- `birth_year` is serialized as an empty value or a four-digit year.
- JSON keeps the result envelope and uses the same normalized record/issue keys.
- TXT remains human-readable and adds labeled enrichment lines only for non-empty
  values.
- Empty results preserve an existing destination exactly as today.

## Profile URL and route policy

> Implementation update: Facebook's current profile UI exposes the relevant
> sections through `directory_*` routes. These replace the older `/about` routes
> in the initial design while preserving the same bounded-navigation policy.

Only normalized Facebook profile URLs discovered by the existing authenticated
parser are eligible.

Numeric identity routes:

```text
https://www.facebook.com/profile.php?id=USER_ID&sk=directory_personal_details
https://www.facebook.com/profile.php?id=USER_ID&sk=directory_links
```

Vanity identity routes:

```text
https://www.facebook.com/USERNAME/directory_personal_details
https://www.facebook.com/USERNAME/directory_links
```

The route builder:

- accepts only `www.facebook.com` identities already normalized by the core URL
  rules;
- rejects login, checkpoint, places, groups, posts, videos, reels, and arbitrary
  external paths;
- removes unrelated query parameters and fragments;
- produces at most two routes per selected profile;
- never follows an externally supplied directory URL without normalization.

## Browser collection

A new optional browser adapter owns profile navigation. It receives the existing
browser, settings, parser, and requested fields. It never constructs or closes a
browser.

For each selected user:

1. Build the bounded personal-details/links directory route list.
2. Navigate to each route once.
3. Wait for document readiness with the existing bounded helper.
4. Check for session-loss routes/cookies after navigation.
5. Capture the in-memory page source and parse it immediately.
6. Merge non-empty details across routes.
7. Apply the configured delay once between profiles, not after the final user.

If one route fails but another yields valid content, the profile is considered a
successful partial enrichment. If all eligible routes fail navigation or parse,
the profile becomes an enrichment issue. Session loss always stops the run.

The adapter does not write HTML, screenshots, cookies, or profile caches.

## Parser strategy

The pure Beautiful Soup parser must not depend on Facebook's obfuscated `x...`
CSS classes or SVG path data.

It uses, in priority order:

1. semantic links (`tel:`, `mailto:`, external website `href`);
2. explicit value/label rows such as `Current city` and `Birthday`;
3. headings and `aria-labelledby` relationships for section context;
4. `role="list"` and `role="listitem"` boundaries;
5. normalized visible text and conservative multilingual phrases;
6. date parsing only inside personal/basic-information fields or sections.

Initial language coverage:

- Vietnamese: `Thông tin cá nhân`, `Thông tin liên hệ`, `Sống ở`, `Đến từ`,
  `tháng` date forms.
- English: `Basic info`, `Contact info`, `Lives in`, `From`, English month names.

Work and education sections are negative fixtures. Years such as employment
start dates or graduation years must not become `birth_year`.

Missing or hidden fields produce empty values, not errors. A structurally valid
About page with no requested visible fields is a successful empty enrichment.

## Service orchestration

Base members/comments collection and global deduplication remain unchanged.
Enrichment runs afterward so a user discovered from multiple targets is visited
only once.

For the first `profile_limit` records:

- call the profile enricher once;
- merge successful values into the immutable `UserRecord`;
- append one sanitized issue when all routes fail;
- continue to the next profile for navigation/parse failures;
- immediately re-raise session failures.

The base record remains in output even when enrichment fails. Enrichment failure
therefore affects enrichment statistics and issues but never discards an already
collected user identity.

## Error and privacy model

- Safe errors include a normalized profile URL but never raw HTML, cookies,
  credentials, proxy credentials, or arbitrary query strings.
- Birth date/year and location are personal data. They are collected only after
  explicit enrichment selection and only when visible to the authorized account.
- The CLI does not persist raw input pages or create an implicit profile cache.
- Runtime exports remain Git-ignored and atomically replaced.
- Operators are responsible for authorization, retention, and downstream use.

## Performance model

Profile enrichment is intentionally slower than base collection. With two About
routes and a three-second inter-profile delay, twenty profiles can require
several minutes. The CLI prints an explicit enrichment summary so this cost is
not mistaken for a stalled process.

One browser and sequential navigation are retained for this phase. Concurrency
belongs in the future job-worker design, where account/session limits can be
enforced centrally.

## API and WebUI readiness

This phase does not implement API/WebUI code, but it establishes their required
contracts:

- a typed enrichment request independent of argparse;
- a normalized record schema shared by all modes;
- progress/coverage statistics suitable for job status;
- profile-level issues that do not discard base records;
- browser ownership outside services;
- no session-cookie exposure in results.

The next API design should be job-oriented (`POST /jobs`, status, records,
cancel, export) rather than holding an HTTP request open for a Selenium run. The
WebUI should consume that job API and use server-sent events or polling for
progress. Session files remain backend-only.

## Testing strategy

All automated tests are offline and synthetic.

- Core tests cover request validation, immutable defaults, merging, and stats.
- URL-table tests cover numeric/vanity About routes and rejection cases.
- Parser fixtures cover Vietnamese and English contact/basic-info sections,
  missing fields, duplicate phones, current city versus address, full birthday,
  year-only birthday, and misleading work/education years.
- Browser-adapter tests use fake browsers/waits and assert bounded route counts,
  delay counts, partial-route success, session loss, and sanitized errors.
- Service integration tests assert enrich-after-dedup, global limit, base-record
  preservation, per-profile isolation, merge behavior, and fatal session loss.
- CLI tests assert explicit flags, validation-before-browser, lazy Selenium
  imports, output, exit codes, and unconditional `quit`.
- Exporter tests assert identical public/authenticated fields for CSV, JSON, and
  XLSX plus atomic preservation.
- No test contains a real user ID, real profile HTML, real session, or live URL
  supplied by an operator.

## Acceptance criteria

- Existing commands behave exactly as before when enrichment is disabled.
- A synthetic profile shaped like the supplied DOM yields current city,
  normalized birthday, and derived birth year without misreading work dates.
- Phone/address fields are populated only from explicit contact/address evidence.
- Each globally unique selected user is enriched at most once.
- Every route, wait, retry, and delay is finite.
- One profile failure does not remove base users or stop other profiles.
- Session loss stops the run with exit code `3`.
- Public CLI construction still imports neither Selenium nor Beautiful Soup.
- Unified output fields match across public and authenticated CSV/JSON/XLSX.
- Full offline tests, compile checks, dependency checks, and repository-safety
  checks pass with no tracked runtime artifacts.

## Alternatives considered

### Parse member/comment HTML more aggressively

Rejected because those pages generally do not contain About fields. Broader
regexes would increase false positives without fixing missing source data.

### Enrich every profile by default

Rejected because it changes command duration, data scope, and privacy behavior
without explicit operator intent.

### Depend on obfuscated CSS classes or icon SVG paths

Rejected because the supplied DOM demonstrates unstable generated class names,
and icon shapes are not a maintainable semantic contract.

### Build API/WebUI first

Rejected because enrichment changes request, result, progress, and output
contracts that API/WebUI would otherwise need to migrate immediately.

### Cache raw profile HTML

Rejected for this phase because it increases sensitive-data retention and secret
hygiene risk. A future database/cache design must define encryption, TTL,
deletion, and authorization explicitly.
