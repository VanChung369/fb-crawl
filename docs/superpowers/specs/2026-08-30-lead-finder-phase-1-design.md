# Lead Finder Phase 1 Product Design

Date: 2026-08-30

## Purpose

Build Lead Finder as a commercial, multi-account Chrome extension backed by
the existing `fb-crawl` FastAPI, PostgreSQL, and FBNumber integration. Phase 1
lets a verified user activate a license, find the phone contact for the
Facebook profile currently open in the browser, review their own lookup
history, and export that history as CSV or XLSX.

The implementation extends `fb-crawl` as a modular monolith. The
`lead-finder` WXT/React project remains a thin browser client. FBNumber
credentials, database credentials, license secrets, and administrative
credentials never enter the extension.

## Confirmed product rules

- Users register and sign in with email and password.
- Email verification is required before license redemption or contact lookup.
- Administrators create license keys. A key may represent 1 day, 7 days,
  1/3/6/12 calendar months, or a custom number of days.
- A license key may be redeemed once and belongs permanently to the account
  that redeemed it.
- Each key carries an administrator-selected monthly contact limit, group
  crawl permission, comment crawl permission, and device limit.
- The default device limit is one unless the administrator selects another
  value on the key.
- A newly redeemed key is scheduled after the account's current licensed
  subscription, so existing paid time is not lost. An administrator may start
  a scheduled subscription immediately through an audited override.
- An account without a currently active paid subscription receives the
  default entitlement: 100 contacts per quota month, one device, and no group
  or comment auto-crawl.
- A quota unit is one Facebook identity whose phone is revealed to that
  account for the first time in the quota month. Repeating the same identity
  in that month does not consume another unit.
- `not_found`, provider failures, refresh attempts, and duplicate lookups do
  not consume quota.
- Contact identity and enrichment evidence form a global shared cache.
- Lookup history, quota consumption, devices, subscriptions, exports, crawl
  jobs, crawl results, and saved leads are account-owned.
- An account cannot browse the global contact store. It sees a contact only
  after its own authorized lookup or crawl has revealed that identity.
- Phase 1 exports account lookup history as UTF-8 CSV and `.xlsx`.
- Phase 2 will add account-owned group-member and post-comment crawl jobs. It
  is intentionally outside this implementation scope.

## Repository and package boundaries

The backend remains in `fb-crawl`:

```text
fb-crawl/
  src/
    fb_crawl/
      accounts/
      auth/
      licenses/
      entitlements/
      contacts/
      history/
      exports/
      admin/
      api/
    fb_data_pipeline/
      providers/
      repositories/
      migrations/
```

The new modules are logical boundaries, not separately deployed services.
Each owns its models, services, repository interfaces, and API composition.
They communicate through typed values rather than importing route handlers or
database implementation details from one another.

Existing boundaries remain authoritative:

- `fb_crawl` owns HTTP composition, account-facing behavior, and collection.
- `fb_data_pipeline` owns FBNumber adaptation, phone normalization, evidence
  merging, and global identity/enrichment persistence.
- `lead-finder` owns Facebook-page detection, browser session UX, and
  presentation. It does not own enrichment or quota business rules.

## Authentication and API policy

The current single `X-API-Key` policy is split into explicit route policies:

```text
Public, rate-limited
  /api/v1/auth/register
  /api/v1/auth/verify-email
  /api/v1/auth/resend-verification
  /api/v1/auth/login
  /api/v1/auth/refresh
  /api/v1/auth/forgot-password
  /api/v1/auth/reset-password

User bearer authentication
  /api/v1/account/*
  /api/v1/devices/*
  /api/v1/licenses/redeem
  /api/v1/contacts/*
  /api/v1/history/*
  /api/v1/exports/*

Administrator web session plus admin role
  /api/v1/admin/*

Existing internal crawler API key
  existing jobs, sessions, proxies, settings, and internal operations

Unauthenticated health probes
  existing liveness/readiness endpoints
```

No master API key is bundled in the extension. Existing internal routes keep
their current API-key boundary until a later, separately reviewed migration.

The account-facing contract also includes:

```text
GET    /api/v1/account/me
DELETE /api/v1/account
GET    /api/v1/account/entitlements
GET    /api/v1/devices
DELETE /api/v1/devices/{device_id}
POST   /api/v1/auth/logout
```

Removing the current device completes the response and then revokes its
session. Removing another device requires recent authentication.

### Passwords and verification

- Passwords are hashed with Argon2id and a production-appropriate work factor.
- Email addresses are trimmed, case-normalized for uniqueness, and preserved
  separately for display if needed.
- Email verification tokens are random, single-use, database-hashed, and
  expire after 24 hours.
- Password-reset tokens are random, single-use, database-hashed, and expire
  after one hour.
- Login and reset responses do not reveal whether an email address exists.
- Changing a password revokes all refresh sessions for that account.

### Sessions and devices

- Access tokens expire after 15 minutes. They are signed JWTs containing
  account, session, and device identifiers only. Role, entitlement, quota, and
  account status are reloaded from server state and are never authoritative
  JWT claims.
- Refresh tokens are opaque random values, stored only as hashes on the
  server, bound to an account and device, rotated on every use, and revocable.
- Reuse of an already rotated refresh token revokes that token family.
- The web application transports its refresh session in a `Secure`,
  `HttpOnly`, `SameSite` cookie and uses CSRF protection for state changes.
- The extension stores its refresh credential in `chrome.storage.local`; only
  the background service worker reads it. The access token is kept in memory
  where practical and refreshed through the background worker.
- On first run, the extension creates a random installation UUID. It does not
  collect hardware fingerprints.
- A device-limit check occurs at login/refresh and when entitlements change.
  If the active limit is lower than the number of devices, additional devices
  are blocked until the user or an administrator revokes an old device.
- Allowed devices are selected deterministically by `first_seen_at, id`; the
  oldest active devices occupy the available slots. A blocked device may
  access account state, device management, logout, and license redemption, but
  may not look up contacts or create exports. This prevents a downgrade from
  locking the user out of the controls needed to recover.
- Suspending an account, revoking a device, or changing a password invalidates
  the applicable refresh sessions.

Email delivery sits behind an `EmailDeliveryPort`. Production uses configured
SMTP credentials or a transactional-email adapter; tests use a fake adapter.
Verification and reset URLs land on small public web views served by the
backend, while the extension polls or refreshes account verification state.

## License and entitlement lifecycle

### License representation

`license_keys` stores a high-entropy key digest, never the plaintext key.
Because generated keys have high entropy and must support exact lookup, the
digest is HMAC-SHA-256 with a server-held pepper and a versioned key ID. This
is distinct from password hashing.

Durations are represented without ambiguity:

- day presets and custom day values use `duration_unit = 'day'`;
- 1/3/6/12-month presets use `duration_unit = 'month'` and calendar-month
  arithmetic;
- the computed `starts_at` and `ends_at` are stored on the subscription.

License entitlement values must be non-negative, the device limit must be at
least one, and duration values must be positive. A zero contact limit is
allowed for an intentionally view-only paid entitlement.

The plaintext license is displayed once in the admin UI immediately after
creation. It cannot be recovered later; an administrator can revoke it and
create another.

### Redemption transaction

`POST /api/v1/licenses/redeem` performs one database transaction:

1. authenticate the verified account and active device;
2. HMAC the supplied key and lock the matching license row;
3. reject missing, revoked, or previously redeemed keys with a safe error;
4. bind the key to the current account permanently;
5. calculate `starts_at` as now when no paid subscription remains, otherwise
   as the latest scheduled paid subscription's `ends_at`;
6. calculate and persist `ends_at` using the key duration;
7. copy the key's quota, crawl permissions, and device limit into an immutable
   entitlement snapshot on the new subscription;
8. write an administrator/user audit event;
9. commit.

The row lock makes concurrent redemption of the same key deterministic. A key
already redeemed by the same or another account is not reusable, including
after the redeeming account is deleted.

An immediate-start administrator override is also atomic. It ends the current
paid subscription at the override timestamp, changes the selected scheduled
subscription to start at that timestamp, and recalculates its end from the new
key's original duration. The admin UI warns that unused time on the current
subscription is forfeited and records both subscription IDs in the audit
event. Only the earliest scheduled subscription may be started this way; all
later valid subscriptions are shifted to remain contiguous after its new end.
This prevents overlaps and accidental gaps in paid entitlements.

### Effective entitlement

The entitlement service derives access at request time:

1. choose the active, non-revoked subscription for the current timestamp;
2. otherwise return the system `default` plan;
3. never trust plan, quota, role, or account values supplied by a client.

No cron job is required to downgrade expired accounts. Once `ends_at <= now`,
the active-subscription query stops selecting that row and the default plan
applies immediately.

Database subscription status records only `valid` or `revoked`. The API
derives `scheduled`, `active`, and `expired` from `starts_at`, `ends_at`, and
the current timestamp, so stored status cannot become stale without a cron
transition.

The quota month is the calendar month in configurable `PRODUCT_TIMEZONE`,
defaulting to `Asia/Ho_Chi_Minh`. Its stored period key is the local first day
of that month. Timestamp storage remains UTC.

All entitlements in a month share the same usage counter. Upgrading raises the
effective limit but does not erase usage already consumed that month. If a
paid subscription expires after usage has exceeded the default limit, the
account cannot reveal another new contact until the next quota month or a new
paid entitlement becomes active.

## Data model

New migrations add the following logical tables. Existing applied migrations
remain immutable.

### Account and authentication tables

```text
accounts
  id, normalized_email, display_email, password_hash
  role(user|admin), status(pending|active|suspended|deleted)
  email_verified_at, created_at, updated_at

account_tokens
  id, account_id, purpose(email_verify|password_reset)
  token_hash, expires_at, consumed_at, created_at

devices
  id, account_id, installation_id, display_name
  status(active|revoked), first_seen_at, last_seen_at

auth_sessions
  id, account_id, device_id, refresh_token_hash
  expires_at, rotated_from_id, revoked_at, created_at, last_used_at

rate_limit_buckets
  bucket_hash, action, window_start, request_count, expires_at
```

`installation_id` is unique within an account. Session and token foreign keys
are indexed and delete with the account according to the retention policy.

### License and entitlement tables

```text
plans
  id, code, name, monthly_contact_limit, max_devices
  allow_group_crawl, allow_comment_crawl, is_system

license_keys
  id, key_digest, key_version, masked_key
  duration_unit, duration_value
  monthly_contact_limit, max_devices
  allow_group_crawl, allow_comment_crawl
  status(available|redeemed|revoked)
  created_by_account_id, redeemed_by_account_id
  redeemed_at, created_at, revoked_at

account_subscriptions
  id, account_id, license_key_id
  starts_at, ends_at, status(valid|revoked)
  monthly_contact_limit, max_devices
  allow_group_crawl, allow_comment_crawl
  created_at, activated_override_at
```

The default plan is a protected system row with 100 monthly contacts, one
device, and both crawl permissions disabled.

### Quota, history, and export tables

```text
usage_monthly
  account_id, period_start, contact_reveal_count, updated_at

account_contact_reveals
  id, account_id, facebook_user_id, period_start
  phone_number_id, lookup_event_id, revealed_at

lookup_events
  id, account_id, device_id, facebook_user_id
  requested_uid, requested_username, requested_profile_url
  outcome(found|not_found|processing|quota_exceeded|failed)
  result_source(cache|provider|negative_cache|none)
  provider_called, quota_charged, safe_error_code
  created_at, completed_at

export_jobs
  id, account_id, format(csv|xlsx), filter_snapshot
  status(queued|running|completed|failed|expired)
  safe_error_code, artifact_path, expires_at
  created_at, completed_at

admin_audit_events
  id, actor_account_id, action, target_type, target_id
  safe_metadata, created_at
```

`account_contact_reveals` has a unique constraint on
`(account_id, facebook_user_id, period_start)`. `usage_monthly` has a primary
key on `(account_id, period_start)`.

`account_contact_reveals.lookup_event_id` is nullable and uses `ON DELETE SET
NULL`; deleting a history event therefore does not delete the reveal or alter
quota accounting. `phone_number_id` remains a restricted reference to the
shared normalized phone.

Quota reservation locks the relevant `usage_monthly` row, checks for an
existing reveal, compares the counter with the effective limit, inserts the
new reveal, and increments the counter in one transaction. This prevents two
concurrent successful lookups from exceeding the account limit.

`phone_number_id` records which normalized phone was revealed without copying
provider raw data. History reads are still authorized through account
ownership and may join current global identity details.

### Shared contact cache state

Existing `facebook_users`, `phone_numbers`, `user_phone_evidence`,
`facebook_user_profiles`, and `enrichment_attempts` remain the source of truth
for global identity and evidence.

New cache coordination state is separate:

```text
provider_lookup_state
  facebook_user_id, provider, field
  latest_status, checked_at, refresh_after
  latest_attempt_id, updated_at

enrichment_leases
  facebook_user_id, provider, field
  owner_token, leased_until, created_at, updated_at
```

Both tables use `(facebook_user_id, provider, field)` as their unique key.
Lease rows coordinate API processes without holding a PostgreSQL transaction
or database lock during an external HTTP call.

## Contact lookup service

### API contract

```http
POST /api/v1/contacts/lookup
Authorization: Bearer <access-token>
Content-Type: application/json
```

```json
{
  "facebook_uid": "100012345678",
  "username": "nguyenvana",
  "profile_url": "https://www.facebook.com/nguyenvana"
}
```

UID is preferred but the request may provide a username/profile URL while the
resolver is completing identity. Inputs use closed schemas, length limits,
Facebook URL validation, and canonical normalization. Conflicting identity
aliases produce a safe conflict response rather than merging two users.

The FBNumber adapter is extended to normalize a provider-returned UID or
username when the authorized response contains one. It accepts that identity
only when it is consistent with the requested aliases; a mismatch is a safe
provider identity conflict and does not modify the stored user.

A completed response includes contact data and account-specific metadata:

```json
{
  "user": {
    "facebook_uid": "100012345678",
    "username": "nguyenvana",
    "name": "Nguyen Van A",
    "profile_url": "https://www.facebook.com/nguyenvana"
  },
  "contact": {
    "phone": "+84981234567"
  },
  "meta": {
    "source": "cache",
    "observed_at": "2026-08-30T10:00:00Z",
    "quota_charged": true,
    "monthly_used": 12,
    "monthly_limit": 100
  }
}
```

The endpoint returns `200` for found and current negative-cache results,
`202` with a lookup event ID when another request owns enrichment longer than
the bounded synchronous wait, `409` for identity conflicts, `429` for account
quota/rate limits, and safe `4xx/5xx` errors for other failures. The extension
polls the lookup event after a `202` response.

### Lookup flow

1. Authenticate the account and device and load effective entitlements.
2. Resolve/upsert the supplied Facebook identity using existing alias locks.
3. Create an account-owned lookup event.
4. If the account already has a reveal for the identity in the current quota
   month, allow the lookup without another quota unit.
5. Otherwise reject an already exhausted account before any provider call.
6. Read global phone evidence and `provider_lookup_state`.
7. Return fresh evidence immediately, subject to atomic quota reservation.
8. Return a current `not_found` state without a provider call or quota charge.
9. For missing/stale data, atomically claim the enrichment lease.
10. The lease owner calls `FBNumberProvider` outside a database transaction.
11. Normalize and validate the result through the existing data pipeline,
    persist evidence/attempt state, update refresh metadata, and release the
    lease.
12. Non-owners wait for a bounded interval and reuse the completed state. If
    the owner is still running, return `202 processing` instead of starting a
    duplicate call.
13. For a found phone, atomically reserve the account reveal. If a concurrent
    request consumed the final quota unit, withhold the phone and return quota
    exhausted.
14. Complete the lookup event with source, quota decision, and safe outcome.

### Cache policy

Initial values are application configuration and may later be editable by an
administrator:

- found phone: fresh for 30 days;
- `not_found`: negative cache for 7 days;
- manual refresh: minimum 24-hour interval for the same identity/provider;
- transport/5xx: existing immediate bounded retry with exponential backoff;
- 429: record rate limiting and honor provider retry metadata;
- provider authentication failure: no repeated loop, safe admin alert.

A manual refresh never bypasses account authorization, quota pre-checks,
provider cooldowns, or the enrichment lease.

## Lookup history and export

### History

```http
GET /api/v1/history/lookups
GET /api/v1/history/lookups/{lookup_event_id}
DELETE /api/v1/history/lookups/{lookup_event_id}
DELETE /api/v1/history/lookups
```

History supports cursor pagination and account-scoped filters for outcome,
name, UID, username, phone, and time range. The repository receives the
authenticated account ID from server context; it never accepts ownership from
query parameters or request bodies.

Reviewing a previously revealed identity does not consume another quota unit
in the same quota month. A lookup in a later quota month follows the normal
reveal rule.

### Export

```http
POST /api/v1/exports
GET /api/v1/exports/{export_id}
GET /api/v1/exports/{export_id}/download
DELETE /api/v1/exports/{export_id}
```

- Supported formats are UTF-8 CSV and Excel `.xlsx`.
- An export captures the current account-owned filter, not arbitrary SQL or a
  client-supplied account ID.
- Values beginning with `=`, `+`, `-`, or `@` are neutralized to prevent
  spreadsheet formula injection.
- Export generation is a background task so large histories do not hold an
  API request open.
- The existing `fb-crawl worker run` process gains a narrow export-job handler
  that claims `export_jobs` independently from crawl jobs. Export claims use a
  lease so process restarts are recoverable and duplicate workers do not
  generate the same artifact concurrently.
- Completed artifacts use unguessable identifiers, require account
  authorization to download, expire after 24 hours, and are then deleted.
- MVP may store artifacts on a dedicated local/attached volume behind an
  `ExportArtifactStore` interface. Object storage can replace it later.
- Provider raw bodies, credentials, internal error detail, and other accounts'
  data never appear in exports.

Account history remains until the user deletes it, deletes the account, or an
administrator applies the documented retention policy. Export artifacts have
the separate 24-hour lifetime above.

Single-event deletion removes that account's event and its event link from a
reveal without decrementing already consumed quota. Bulk deletion applies the
current account-owned filter and follows the same rule. Deleting history is
therefore a privacy operation, not a way to restore monthly quota.

## Extension architecture

The existing `lead-finder` WXT/React starter becomes a Manifest V3 extension
with a Side Panel as its primary interface.

```text
entrypoints/
  background.ts
  content/
    facebook-detector.ts
    identity-resolver.ts
  sidepanel/
    App.tsx
api/
  auth-client.ts
  contact-client.ts
  history-client.ts
  export-client.ts
storage/
  session-storage.ts
  device-storage.ts
shared/
  messages.ts
  types.ts
```

### Facebook detection and identity resolution

Facebook is a single-page application, so the detector observes route changes
and relevant DOM mutations. It distinguishes personal profiles from groups,
pages, posts, Marketplace, and system routes.

Identity resolution uses this order:

1. read a numeric UID from a valid `profile.php?id=...` route;
2. read the expected username from a valid vanity profile route;
3. inspect page-visible canonical/embedded identity data for a UID paired with
   that same username;
4. accept a UID only when the route username and embedded username agree;
5. reject multiple conflicting UIDs instead of guessing;
6. send a partial username/profile URL to the backend when a numeric UID is
   unavailable so the provider can complete identity if supported.

Phase 1 does not copy FBNumber's global GraphQL/XHR interception bridge. That
approach is fragile and exposes more page data than the one-profile workflow
needs. A narrow main-world bridge requires separate evidence and review if the
tested resolver proves insufficient.

### Browser responsibilities

- The content script reports the current identity candidate only.
- The background service worker owns authentication refresh, device ID, API
  calls, retry/poll coordination, and cross-tab state.
- The Side Panel owns registration, verification guidance, login, license
  redemption, Find Contact, copy, quota display, history, and export UX.
- Navigating to a profile updates Side Panel context but never performs a
  lookup automatically. The user must press **Find Contact**.
- Switching profiles cancels or detaches stale UI requests so a response for
  the previous profile cannot be shown as the current profile.

Requested permissions remain minimal: `storage`, `sidePanel`, Facebook host
access, and the configured Lead Finder backend host. Production extension IDs
and web origins are explicit allowlists.

## Admin dashboard

The existing `fb-crawl` dashboard gains an administrator-only section for:

- creating and revoking license keys;
- choosing duration, monthly quota, crawl permissions, and device limit;
- viewing the one-time plaintext generated key;
- viewing/suspending accounts and revoking devices/sessions;
- inspecting subscriptions and applying an audited immediate-start override;
- viewing aggregate quota, cache, and provider health metrics;
- reviewing safe error codes and audit events.

Admin routes verify the server-side admin role in addition to authentication.
Hiding navigation links is not authorization.

The first administrator is created with a one-time `fb-crawl admin bootstrap`
command that requires direct database configuration and securely prompts for
the password. It refuses to create another bootstrap admin after one exists.
Passwords and plaintext license keys are never accepted as command-line
arguments.

## Security and privacy

- Production traffic uses HTTPS only.
- Registration, login, token refresh, reset, redeem, lookup, export, and admin
  operations have endpoint-appropriate account/device/IP rate limits.
- MVP rate-limit buckets are PostgreSQL-backed so multiple API processes share
  decisions without Redis. IP bucket keys are HMACed with a rotating server
  secret rather than retained as indefinite raw IP history.
- CORS allows only configured web origins. Extension requests are restricted
  to configured production extension IDs and backend host permissions.
- Logs remove passwords, tokens, license plaintext, cookies, DSNs, provider
  tokens, and provider raw responses.
- Safe error codes are stored; arbitrary provider error bodies are not.
- Database roles use least privilege. Production database volumes and backups
  are encrypted and access-audited.
- All important admin and license actions create immutable audit events.
- Account deletion revokes sessions immediately and starts the configured
  personal-data deletion workflow.
- Shared cache retention and deletion are governed separately from
  account-owned history because other accounts may rely on the same evidence.
- The product collects only data required for the authorized workflow and
  must be operated consistently with source terms and applicable privacy law.

Account deletion is two-stage: the account is immediately marked deleted and
all access is revoked, then account-owned history, devices, sessions,
subscriptions, and exports are hard-deleted after the configured recovery
window. Shared identity/evidence is not cascaded from an account deletion.

## Observability

Metrics distinguish product usage from provider cost:

```text
lookup_count
unique_contact_reveals
cache_hit_count and cache_hit_rate
negative_cache_hit_count
provider_request_count
provider_found/not_found/failed/rate_limited
provider_latency
quota_rejections
active_accounts, licenses, and devices
export_success/failure/latency
```

Structured logs carry a request correlation ID, account-safe identifier, and
safe error code. They do not carry contact values unless an explicitly
protected diagnostic policy requires them.

## Failure behavior

- Invalid credentials return one generic authentication error.
- Unverified accounts may log in only far enough to resend verification; they
  cannot redeem or look up contacts.
- Expired paid subscriptions fall back to the default plan at request time.
- Quota exhaustion is checked before a provider call when the identity has no
  current account reveal.
- Provider `not_found` persists negative state and does not consume quota.
- Provider failure preserves existing identity/evidence and records a safe
  attempt for later retry.
- A crashed enrichment owner loses its lease after `leased_until`; another
  request may then claim it.
- Export failure does not affect history; the user may retry.
- Account A receives `404` rather than ownership detail when requesting an
  export or history item belonging to account B.

## Testing strategy

Implementation follows red-green-refactor.

### Backend unit tests

- email normalization, Argon2 password verification, token hashing/expiry;
- refresh rotation, reuse detection, device revocation, and device limits;
- license generation, HMAC lookup, duration arithmetic, one-time redemption,
  queueing, expiration fallback, and administrator override;
- entitlement selection and quota-month calculation;
- atomic reveal deduplication and quota-limit behavior;
- cache freshness, negative caching, refresh limits, and lease expiry;
- lookup outcome/source mapping and safe errors;
- history filter validation and export formula neutralization.

### PostgreSQL integration tests

- concurrent redemption allows exactly one winner;
- concurrent quota reservations never exceed the monthly limit;
- concurrent enrichment claims produce one lease owner;
- expired leases can be reclaimed;
- global evidence can serve multiple accounts while reveal/history ownership
  remains isolated;
- subscription expiration selects the default plan without a cron job;
- deleting an account cascades only account-owned data, not shared evidence.

### API authorization tests

- public, bearer, admin, and existing API-key routes enforce distinct policy;
- an unverified/suspended account cannot redeem, look up, or export;
- account A cannot list, read, delete, or download account B's resources;
- client-supplied account/role/quota values are rejected or ignored;
- rate limits and safe error envelopes are stable.

### Extension tests

- route classification rejects non-profile Facebook surfaces;
- numeric routes and vanity routes resolve correctly;
- embedded UID must match the expected username;
- conflicting UIDs are rejected;
- SPA navigation updates context and discards stale responses;
- background refresh/session flows do not expose credentials to content code;
- Side Panel states cover authentication, verification, license, quota,
  processing, found, not found, and provider failure.

### End-to-end acceptance

```text
register
  -> verify email
  -> log in from installation device
  -> redeem administrator-created key
  -> open Facebook profile
  -> Find Contact
  -> view quota and lookup history
  -> repeat same UID without another quota charge
  -> export filtered history as CSV and XLSX
```

The complete existing `fb-crawl` test suite and `lead-finder` TypeScript/build
checks must remain green.

## Phase 2 contract and deferred scope

Phase 2 receives a separate design and implementation plan. Its approved
product contract is:

- paid entitlements may independently enable group-member and post-comment
  auto-crawl;
- the extension detects a group or post and offers an explicit crawl action;
- each crawl job, result, history entry, and export belongs to its creator
  account;
- the existing `fb-crawl` job engine and worker are reused;
- enrichment uses the same global cache, lease, and account quota service;
- a phone revealed for a new UID consumes one monthly unit; duplicates,
  `not_found`, and failures do not;
- enrichment stops at quota exhaustion and the job becomes `partial_quota`;
- already collected identity data remains available and exportable;
- crawl history reports target, progress, identities found, contacts revealed,
  and safe failure status;
- per-job and filtered history export support CSV and XLSX.

Also deferred:

- Google/social login;
- payment processing and automatic billing;
- multiple enrichment providers and provider fallback;
- saved-lead workflow, tags, notes, CRM, webhooks, and Google Sheets;
- native mobile/browser support beyond the WXT targets selected for release;
- Redis, Kafka, microservices, and distributed object storage;
- interception of all Facebook GraphQL/XHR traffic.

## Delivery boundary

This specification is intentionally the Phase 1 delivery boundary. It is
large but cohesive: every component is required for a secure commercial
single-profile lookup product. Phase 2 crawling is recorded only as a stable
integration contract and must not be pulled into the Phase 1 implementation
plan.
