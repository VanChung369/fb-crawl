# Authenticated Profile Enrichment Implementation Plan

**Goal:** Add explicit, bounded profile enrichment to authenticated members,
comments, and batch runs so visible phone, website, address, location, and
birthday fields can be collected without changing default command behavior.

**Architecture:** Keep base identity collection unchanged. After global user
deduplication, an optional service stage calls a Selenium-backed profile enricher
for a bounded subset of unique users. The adapter visits normalized About routes,
uses a pure multilingual parser, and returns typed `ProfileDetails`; the service
merges them into immutable `UserRecord` values and records coverage statistics.

**Tech Stack:** Python 3.12+, argparse, Selenium 4 Firefox driver, Beautiful Soup
4, dataclasses, protocols, pytest, atomic CSV/JSON/TXT/XLSX exporters.

**Design spec:**
[`docs/superpowers/specs/2026-08-08-authenticated-profile-enrichment-design.md`](../specs/2026-08-08-authenticated-profile-enrichment-design.md)

## Prerequisite

The current unified output-schema work must be reviewed and committed before
starting Task 1. It introduces `src/fb_crawl/exporters/schema.py`, the common
public/authenticated columns, and public address parsing. Do not mix unrelated
working-tree changes into enrichment commits.

Baseline verification:

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
git diff --check
git status --short
```

Expected baseline at the time this plan was written: `118 passed` and no broken
requirements.

## Global constraints

- Work only in `D:/project/fb/fb-crawl`.
- Never copy the real supplied profile DOM, real user ID, real session, runtime
  output, cookies, or personal data into tracked files.
- Synthetic fixtures must use invented identities, locations, phone numbers,
  domains, and dates.
- Enrichment remains disabled unless the operator selects it explicitly.
- Existing public and authenticated commands retain their behavior when
  enrichment is disabled.
- Public CLI construction must not import Selenium or Beautiful Soup.
- Use only normalized Facebook profile routes; reject external/internal targets.
- Never bypass login, privacy, CAPTCHA, checkpoint, or two-factor controls.
- Every profile count, route count, wait, retry, click, and delay is finite.
- Use one browser sequentially; no browser pools or parallel profile navigation.
- Missing fields are successful empty data, not guessed values and not failures.
- A profile-level navigation/parse failure preserves the base user and allows
  other profiles to continue.
- Session loss is fatal for the whole run.
- Do not persist raw HTML or add a profile cache in this phase.
- Preserve unrelated user edits and stage only files named by each task.

---

## Locked file map

| File | Responsibility |
|---|---|
| `src/fb_crawl/core/models.py` | Profile field/options/details, enriched user record, stats. |
| `src/fb_crawl/core/__init__.py` | Export new public core contracts. |
| `src/fb_crawl/core/urls.py` | Pure normalized numeric/vanity About routes. |
| `src/fb_crawl/adapters/browser/profile_parser.py` | Pure multilingual About HTML parser. |
| `src/fb_crawl/adapters/browser/profiles.py` | Bounded profile-route navigation and detail merge. |
| `src/fb_crawl/services/authenticated.py` | Enrich-after-dedup orchestration and isolation. |
| `src/fb_crawl/cli/authenticated.py` | Opt-in flags, request mapping, lazy composition, summary. |
| `src/fb_crawl/exporters/schema.py` | Unified enrichment columns and row mapping. |
| `src/fb_crawl/exporters/json.py` | Optional enrichment statistics in JSON envelope. |
| `src/fb_crawl/exporters/users.py` | Enriched CSV/TXT/XLSX serialization. |
| `tests/fixtures/authenticated/profile_about_vi.html` | Synthetic Vietnamese About structure. |
| `tests/fixtures/authenticated/profile_contact_en.html` | Synthetic English contact structure. |
| `tests/unit/core/test_models.py` | Core validation/default/merge contracts. |
| `tests/unit/core/test_authenticated_urls.py` | About-route normalization table. |
| `tests/unit/adapters/browser/test_profile_parser.py` | Pure parser coverage and false-positive tests. |
| `tests/unit/adapters/browser/test_profiles.py` | Bounded fake-browser navigation tests. |
| `tests/integration/test_authenticated_service.py` | Dedup, limit, isolation, stats, session loss. |
| `tests/unit/cli/test_authenticated_parser.py` | CLI option/request contracts. |
| `tests/integration/test_authenticated_cli.py` | Runtime wiring, output, cleanup, exit codes. |
| `tests/unit/exporters/test_user_exporters.py` | Common schema and enriched serialization. |
| `tests/unit/exporters/test_csv_exporter.py` | Public rows remain schema-compatible. |
| `docs/authenticated-cli.md` | Operator options, performance, privacy, troubleshooting. |
| `docs/public-cli.md` | Final shared schema documentation. |
| `README.md` | Short enrichment entry point. |

Task dependencies are linear: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8.

---

## Task 1: Core enrichment contracts and unified schema fields

**Files:**

- Modify: `src/fb_crawl/core/models.py`
- Modify: `src/fb_crawl/core/__init__.py`
- Modify: `src/fb_crawl/exporters/schema.py`
- Modify: `tests/unit/core/test_models.py`
- Modify: `tests/unit/exporters/test_user_exporters.py`
- Modify: `tests/unit/exporters/test_csv_exporter.py`

**Produces:** `ProfileField`, `ProfileDetails`, `EnrichmentStats`, enriched
`UserRecord`, enrichment request settings, and the final unified field order.

- [ ] **Step 1: Add failing core contract tests**

Cover:

- `ScrapeRequest` defaults to enrichment disabled;
- `profile_fields` without `enrich_profiles=True` is rejected;
- zero/negative `profile_limit` is rejected;
- negative `profile_delay_seconds` is rejected;
- duplicate `ProfileField` values are rejected;
- old `UserRecord(...)` constructors still work with empty enrichment defaults;
- `ProfileDetails` and `EnrichmentStats` are immutable;
- a full birthday uses ISO `YYYY-MM-DD` and birth year is an integer.

Core shape:

```python
class ProfileField(StrEnum):
    PHONE = "phone"
    WEBSITE = "website"
    ADDRESS = "address"
    CURRENT_CITY = "current_city"
    HOMETOWN = "hometown"
    BIRTH_DATE = "birth_date"


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

- [ ] **Step 2: Run focused tests and confirm missing contracts fail**

```powershell
python -m pytest tests/unit/core/test_models.py tests/unit/exporters/test_user_exporters.py -q
```

- [ ] **Step 3: Implement immutable contracts and validation**

Extend `ScrapeRequest` with:

```python
enrich_profiles: bool = False
profile_fields: tuple[ProfileField, ...] = ()
profile_limit: int = 20
profile_delay_seconds: float = 3.0
```

Extend `UserRecord` with the `ProfileDetails` fields using compatible defaults.
Add optional `enrichment: EnrichmentStats | None = None` to `ScrapeResult` after
the existing required fields. Export new public contracts from `core/__init__.py`.

- [ ] **Step 4: Extend the unified schema and row mappings**

Final field order:

```text
user_id,name,username,page_name,category,website,address,current_city,hometown,birth_date,birth_year,phone_numbers,phone_sources,profile_url,source,source_url,error_code,error_message
```

Public rows leave profile-only fields empty. Authenticated rows join tuple phone
values with `; `, serialize `birth_year` as text, and leave unknown fields empty.
Issue rows contain every field.

- [ ] **Step 5: Run focused and full tests**

```powershell
python -m pytest tests/unit/core/test_models.py tests/unit/exporters -q
python -m pytest -q
git diff --check
```

- [ ] **Step 6: Commit Task 1 only**

```powershell
git add -- src/fb_crawl/core/models.py src/fb_crawl/core/__init__.py src/fb_crawl/exporters/schema.py tests/unit/core/test_models.py tests/unit/exporters/test_user_exporters.py tests/unit/exporters/test_csv_exporter.py
git diff --cached --check
git commit -m "feat: add profile enrichment contracts"
```

---

## Task 2: Pure normalized profile About routes

**Files:**

- Modify: `src/fb_crawl/core/urls.py`
- Modify: `tests/unit/core/test_authenticated_urls.py`

**Produces:** `profile_about_urls(profile_url, user_id) -> tuple[str, ...]`.

- [ ] **Step 1: Add failing route-table tests**

Required cases:

```text
profile.php?id=123
  -> profile.php?id=123&sk=about
  -> profile.php?id=123&sk=about_contact_and_basic_info

/synthetic.user
  -> /synthetic.user/about
  -> /synthetic.user/about_contact_and_basic_info
```

Also cover mobile hosts, fragments, unrelated query parameters, normalized
handles, and stable route order.

Reject:

- external hosts;
- login/checkpoint/two-step paths;
- groups, posts, videos, reels, places, and arbitrary internal paths;
- mismatched numeric `user_id` and profile query ID;
- malformed/empty identities.

- [ ] **Step 2: Implement route construction using existing host/path rules**

Do not accept arbitrary About URLs directly. Normalize the base identity first,
then construct at most the two locked routes.

- [ ] **Step 3: Run URL suites**

```powershell
python -m pytest tests/unit/core/test_authenticated_urls.py tests/unit/core/test_urls.py -q
python -m pytest -q
```

- [ ] **Step 4: Commit Task 2 only**

```powershell
git add -- src/fb_crawl/core/urls.py tests/unit/core/test_authenticated_urls.py
git diff --cached --check
git commit -m "feat: add profile about URL routes"
```

---

## Task 3: Pure multilingual profile details parser

**Files:**

- Create: `src/fb_crawl/adapters/browser/profile_parser.py`
- Create: `tests/unit/adapters/browser/test_profile_parser.py`
- Create: `tests/fixtures/authenticated/profile_about_vi.html`
- Create: `tests/fixtures/authenticated/profile_contact_en.html`

**Produces:** `ProfileParser.parse(html, source_url, requested_fields) -> ProfileDetails`.

- [ ] **Step 1: Create synthetic fixtures**

The Vietnamese fixture should contain invented data:

- heading `Thông tin cá nhân` linked to a `role="list"`;
- `Sống ở Thành phố Ví Dụ`;
- `Đến từ Tỉnh Ví Dụ`;
- birthday `2 tháng 1, 1990`;
- work date `4 tháng 12, 2022 - Hiện tại`;
- education text `Tốt nghiệp năm 2019`.

The English contact fixture should contain:

- heading `Contact info`;
- a synthetic `tel:` link and repeated visible phone text;
- an external `https://profile.example.test` website;
- a synthetic street address;
- no real domains, people, locations, IDs, or Facebook HTML copied from input.

- [ ] **Step 2: Write failing parser tests**

Assert:

- Vietnamese current city and hometown lose only their labels;
- birthday normalizes to `1990-01-02` and year `1990`;
- work/education years never become birth year;
- duplicate phone values collapse in discovery order;
- phone sources contain only safe source labels/normalized route URLs;
- website strips Facebook tracking query parameters where applicable;
- address remains distinct from current city;
- field filtering omits unrequested values;
- valid empty About HTML returns empty `ProfileDetails`;
- parser does not require any `x...` CSS class.

- [ ] **Step 3: Implement section-aware parsing**

Implementation rules:

- normalize Unicode and whitespace;
- resolve section context from heading IDs and `aria-labelledby`;
- inspect semantic links before visible-text fallbacks;
- parse phone text only inside contact sections;
- parse birthday only inside basic/personal-information sections;
- support locked Vietnamese and English phrases/month names;
- validate calendar dates with `datetime.date`;
- accept a year-only birthday only when the surrounding section is personal
  information and the value is plausible;
- never inspect CSS class values or SVG path/icon content.

- [ ] **Step 4: Run parser and existing user-parser suites**

```powershell
python -m pytest tests/unit/adapters/browser/test_profile_parser.py tests/unit/adapters/browser/test_user_parser.py -q
python -m pytest -q
```

- [ ] **Step 5: Commit Task 3 only**

```powershell
git add -- src/fb_crawl/adapters/browser/profile_parser.py tests/unit/adapters/browser/test_profile_parser.py tests/fixtures/authenticated/profile_about_vi.html tests/fixtures/authenticated/profile_contact_en.html
git diff --cached --check
git commit -m "feat: parse visible profile details"
```

---

## Task 4: Bounded Selenium profile enricher

**Files:**

- Create: `src/fb_crawl/adapters/browser/profiles.py`
- Create: `tests/unit/adapters/browser/test_profiles.py`
- Modify: `src/fb_crawl/adapters/browser/__init__.py`

**Produces:** `ProfileEnricher.enrich(browser, record, fields) -> ProfileDetails`.

- [ ] **Step 1: Write fake-browser navigation tests**

Cover:

- exactly two normalized routes for numeric and vanity identities;
- one readiness wait per route;
- parser receives in-memory page source and normalized route source;
- no browser construction or `quit` in the adapter;
- values from overview/contact routes merge without duplicates;
- one failed route plus one successful route returns partial details;
- both failed routes raise a sanitized `BrowserNavigationError`;
- structurally invalid parsing on every route raises `BrowserParseError`;
- login/checkpoint/two-step redirect or missing authenticated cookie raises
  `SessionError` immediately;
- no unbounded scroll/click/retry loop exists.

- [ ] **Step 2: Implement the adapter**

Reuse:

- `profile_about_urls` for target construction;
- `wait_for_document_ready` for bounded readiness;
- `is_authenticated` for session validation;
- `ProfileParser` for pure parsing.

Catch only route-level navigation/parser errors. Never catch `SessionError`.
Keep HTML only in local memory for the duration of a parse call.

- [ ] **Step 3: Run browser adapter suites**

```powershell
python -m pytest tests/unit/adapters/browser/test_profiles.py tests/unit/adapters/browser/test_driver.py tests/unit/adapters/browser/test_session.py -q
python -m pytest -q
```

- [ ] **Step 4: Commit Task 4 only**

```powershell
git add -- src/fb_crawl/adapters/browser/profiles.py src/fb_crawl/adapters/browser/__init__.py tests/unit/adapters/browser/test_profiles.py
git diff --cached --check
git commit -m "feat: add bounded profile enrichment adapter"
```

---

## Task 5: Enrich-after-dedup service orchestration

**Files:**

- Modify: `src/fb_crawl/services/authenticated.py`
- Modify: `tests/integration/test_authenticated_service.py`

**Produces:** globally deduplicated, optionally enriched `UserRecord` values and
`EnrichmentStats`.

- [ ] **Step 1: Extend service ports and fake collaborators**

Add a narrow optional port:

```python
class ProfileEnricherPort(Protocol):
    def enrich(
        self,
        browser,
        record: UserRecord,
        fields: tuple[ProfileField, ...],
    ) -> ProfileDetails: ...
```

Inject a sleeper callable for deterministic delay tests. Keep constructor
compatibility for existing tests/runtimes where enrichment is disabled.

- [ ] **Step 2: Write failing integration tests**

Cover:

- enrichment disabled makes zero profile calls;
- duplicate users across targets are enriched once;
- global `profile_limit` selects only the first unique records;
- requested fields are passed exactly;
- successful details merge into the base record;
- tuple phones/sources union in stable order;
- scalar merge keeps first non-empty value;
- empty details count as successful enrichment;
- navigation/parse failure preserves the base record and appends one issue with
  action `profile_enrichment`;
- other users continue after profile failure;
- session failure is re-raised and stops later profiles;
- delay occurs exactly between attempted profiles, never after the final one;
- coverage statistics count actual non-empty fields.

- [ ] **Step 3: Implement orchestration after global deduplication**

Do not enrich inside each target loop. Build the current globally deduplicated
records first, then select the bounded prefix. Preserve base `ScrapeStats` and
attach `EnrichmentStats` separately.

- [ ] **Step 4: Run service and full suites**

```powershell
python -m pytest tests/integration/test_authenticated_service.py -q
python -m pytest -q
```

- [ ] **Step 5: Commit Task 5 only**

```powershell
git add -- src/fb_crawl/services/authenticated.py tests/integration/test_authenticated_service.py
git diff --cached --check
git commit -m "feat: orchestrate profile enrichment"
```

---

## Task 6: CLI flags, lazy runtime wiring, and progress summary

**Files:**

- Modify: `src/fb_crawl/cli/authenticated.py`
- Modify: `tests/unit/cli/test_authenticated_parser.py`
- Modify: `tests/unit/cli/test_public_parser.py`
- Modify: `tests/integration/test_authenticated_cli.py`

- [ ] **Step 1: Write failing CLI parser tests**

Cover all members/comments/batch commands:

- flags are absent from behavior unless selected;
- comma-separated fields normalize to ordered unique `ProfileField` values;
- unknown/empty fields return exit code `2` before browser creation;
- fields without `--enrich-profiles` return exit code `2`;
- profile limit and delay map into the typed request;
- invalid bounds return exit code `2` before browser creation.

- [ ] **Step 2: Add optional-dependency-free arguments**

Add to authenticated `_common`:

```text
--enrich-profiles
--profile-fields
--profile-limit
--profile-delay
```

The module-level parser code must continue to avoid imports from
`adapters/browser` and `exporters/users`.

- [ ] **Step 3: Wire the profile adapter lazily**

Inside `_load_runtime`, import `ProfileParser` and `ProfileEnricher`, pass the
enricher to `AuthenticatedService`, and inject `time.sleep` at composition time.
Sanitize missing Beautiful Soup/Selenium errors as today.

- [ ] **Step 4: Extend CLI output and cleanup tests**

When enrichment is requested, append a concise summary:

```text
enrichment_selected=N enrichment_succeeded=N enrichment_failed=N phone_found=N current_city_found=N birth_year_found=N
```

Assert browser `quit` on success, profile failure, exporter failure, and session
failure. Public isolated-process import tests must remain green.

- [ ] **Step 5: Run CLI suites**

```powershell
python -m pytest tests/unit/cli/test_authenticated_parser.py tests/unit/cli/test_public_parser.py tests/integration/test_authenticated_cli.py -q
python -m pytest -q
```

- [ ] **Step 6: Commit Task 6 only**

```powershell
git add -- src/fb_crawl/cli/authenticated.py tests/unit/cli/test_authenticated_parser.py tests/unit/cli/test_public_parser.py tests/integration/test_authenticated_cli.py
git diff --cached --check
git commit -m "feat: expose profile enrichment CLI"
```

---

## Task 7: Exporters and operator documentation

**Files:**

- Modify: `src/fb_crawl/exporters/json.py`
- Modify: `src/fb_crawl/exporters/users.py`
- Modify: `tests/unit/exporters/test_user_exporters.py`
- Modify: `tests/unit/exporters/test_json_exporter.py`
- Modify: `docs/authenticated-cli.md`
- Modify: `docs/public-cli.md`
- Modify: `README.md`

- [ ] **Step 1: Add failing enriched exporter tests**

Assert:

- CSV and XLSX use the exact locked field order;
- phone tuples serialize with `; `;
- ISO birth date and four-digit birth year serialize consistently;
- current city, hometown, and address remain separate;
- JSON record keys match CSV and includes optional enrichment statistics;
- TXT prints only non-empty requested values and safe issue lines;
- empty results preserve existing destinations;
- failed XLSX writes preserve existing files and remove temporaries.

- [ ] **Step 2: Implement enriched row/envelope/text serialization**

No exporter derives fields from unrelated text. It serializes typed values from
the model only. Keep XLSX import lazy and all writes atomic.

- [ ] **Step 3: Update operator documentation**

Document:

- opt-in commands and every new flag;
- the two About routes per selected profile;
- expected time cost and global limit;
- field semantics and final schema;
- missing/hidden versus failed enrichment;
- birth date/location privacy and retention guidance;
- why class-based selectors are not supported;
- manual authorized-account checks without real data examples.

- [ ] **Step 4: Run exporter/docs smoke checks**

```powershell
python -m pytest tests/unit/exporters -q
fb-crawl authenticated members --help
fb-crawl authenticated comments --help
fb-crawl authenticated batch --help
python -m pytest -q
```

- [ ] **Step 5: Commit Task 7 only**

```powershell
git add -- src/fb_crawl/exporters/json.py src/fb_crawl/exporters/users.py tests/unit/exporters/test_user_exporters.py tests/unit/exporters/test_json_exporter.py docs/authenticated-cli.md docs/public-cli.md README.md
git diff --cached --check
git commit -m "docs: document profile enrichment"
```

---

## Task 8: Repository safety and final verification

**Files:**

- Modify only if required: `tests/unit/test_repository_safety.py`

- [ ] **Step 1: Re-run repository-safety checks**

Ensure no new tracked paths can contain profile HTML, screenshots, profile
caches, session data, or enriched runtime output.

```powershell
python -m pytest tests/unit/test_repository_safety.py -q
git ls-files | rg "(^|/)(runtime|profile.*\.html$|session\.json)|geckodriver\.log|firefox\.log|results\.(csv|json|txt|xlsx)$"
```

The tracked search may list only the two explicitly synthetic fixture paths; it
must list no runtime or real-profile artifact.

- [ ] **Step 2: Run the complete offline matrix**

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
python -c "from fb_crawl.cli.app import build_parser; build_parser(); import sys; assert 'selenium' not in sys.modules; assert 'bs4' not in sys.modules"
git diff --check
git status --short
```

- [ ] **Step 3: Run legacy behavioral regression tests read-only**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& 'D:\project\fb\fb-crawl\.venv\Scripts\python.exe' `
  -m pytest tests `
  --ignore=tests/test_repository_safety.py `
  -p no:cacheprovider `
  -q
```

Run from `D:/project/fb/Facebook-Data-Scraping-Tools`. Do not edit that project.

- [ ] **Step 4: Inspect changes and commit safety updates if any**

```powershell
git status --short
git diff --check
git diff --cached --check
```

If the repository-safety test required a change:

```powershell
git add -- tests/unit/test_repository_safety.py
git commit -m "test: protect profile enrichment artifacts"
```

- [ ] **Step 5: Record optional manual smoke checks without running them**

With an authorized test account, the operator may separately verify:

```text
1. A command without --enrich-profiles performs no profile navigation.
2. Visible enrichment collects only fields manually visible to that account.
3. Current city is not exported as a street address.
4. A full visible birthday produces ISO birth_date and matching birth_year.
5. A profile without a visible phone leaves phone fields empty.
6. A failed profile preserves the base user and later profiles continue.
7. Session loss stops the run and Firefox exits.
8. Git status contains no session, HTML, screenshot, cache, or output artifact.
```

These checks are never automated against Facebook and are never initiated by an
agent without the operator explicitly starting the authorized login.

## Final implementation evidence

Before declaring the enrichment phase complete, record exact outputs of:

```powershell
git log --oneline -12
python -m pytest -q
python -m compileall -q src tests
python -m pip check
git status --short
```

The phase is complete only when default behavior is unchanged, enrichment is
explicit and bounded, the synthetic DOM shapes parse correctly, session loss is
fatal, base records survive profile failures, normalized schemas match across
modes/formats, browser extras remain lazily imported, and no sensitive/generated
artifact is tracked.

## Follow-on design order

After this plan is implemented and manually smoke-tested:

1. Write the asynchronous API/job design (`POST /jobs`, status, progress,
   records, cancel, export, backend-only session handling).
2. Write the WebUI design against that API (job creation, progress, filters,
   record table, field coverage, issues, downloads).
3. Define database retention/encryption before persisting enriched personal data.

Do not start WebUI implementation directly against CLI subprocess output.
