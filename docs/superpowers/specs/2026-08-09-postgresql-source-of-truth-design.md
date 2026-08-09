# PostgreSQL Source-of-Truth Design

Date: 2026-08-09

## Purpose

Make PostgreSQL the durable source of truth for Facebook identities and phone
evidence produced by `fb_crawl` and FBNumber. CSV and other exported artifacts
remain transient compatibility outputs and are not authoritative.

This design uses regular PostgreSQL 17 and `psycopg`. It does not depend on
Supabase services, SDKs, authentication, or APIs.

## Confirmed product rules

- `phone_1` is the preferred normalized phone returned by FBNumber.
- `phone_2` is the preferred normalized phone observed directly by `fb_crawl`.
- `phone_1` and `phone_2` are derived by a PostgreSQL view. They are not
  duplicated as writable columns on the user table.
- All phone evidence remains available even when it is not selected for a
  preferred slot.
- A failed, rate-limited, or not-found FBNumber call does not discard the
  Facebook user or `phone_2` evidence.
- Provider tokens and raw response bodies are never stored or logged.

## Package boundary

The implementation remains in the single `fb-crawl` distribution:

```text
fb-crawl/
  src/
    fb_crawl/
    fb_data_pipeline/
      migrations/
      repositories/
```

`fb_crawl` owns collection. `fb_data_pipeline` owns provider normalization,
source-aware merging, migrations, and PostgreSQL persistence. The packages
communicate through typed `ScrapeResult`, `UserBundle`, `PhoneEvidence`, and
`ProviderResult` objects rather than CSV.

## PostgreSQL deployment

- PostgreSQL 17 is available through the repository Compose configuration.
- The application connects with `psycopg` using `DATABASE_URL` from the
  environment.
- Production deployments use a non-superuser application role and an external
  connection pooler such as PgBouncer in transaction mode.
- Migrations are packaged inside `fb_data_pipeline.migrations` and tracked in
  `schema_migrations`.
- Application transactions set a bounded local statement timeout.

## Schema

All identifiers use lowercase snake_case. Primary keys are sequential `bigint`
identity columns. Times use `timestamptz`.

### `facebook_users`

Stores the current normalized identity:

- `id bigint generated always as identity primary key`;
- nullable unique `facebook_uid`;
- nullable `facebook_username` and unique normalized username;
- nullable `display_name`;
- nullable unique canonical `profile_url`;
- `created_at` and `updated_at`.

At least one UID, normalized username, or profile URL is required. Blank
strings are converted to SQL `NULL` before persistence.

### `phone_numbers`

Stores each normalized phone once:

- `id bigint generated always as identity primary key`;
- unique `normalized_phone` text;
- first-seen `display_phone` text;
- `created_at`.

The normalized value has a check constraint for an E.164-like `+` followed by
8 to 15 digits.

### `user_phone_evidence`

Stores the complete auditable relationship between an identity and a phone:

- foreign keys to `facebook_users` and `phone_numbers`;
- `origin` constrained to `fbnumber` or `fb_crawl`;
- source name and source URL;
- provider name and correlation ID;
- confidence;
- first and last capture times;
- evidence count and audit timestamps.

The unique evidence key is:

```text
facebook_user_id + phone_number_id + origin + source + source_url + provider
```

Foreign-key columns are indexed. A composite user/origin index supports the
preferred-slot view.

### `enrichment_attempts`

Stores a safe diagnostic row for every FBNumber call:

- foreign key to `facebook_users`;
- provider;
- status constrained to `found`, `not_found`, `rate_limited`, or `failed`;
- checked timestamp;
- correlation ID;
- safe error code;
- number of provider values found.

The table does not contain credentials, request payloads, or raw responses. A
composite index on user, provider, and descending checked time supports retry
and audit queries.

### `facebook_user_phone_slots` view

The view exposes user identity plus two nullable normalized phone columns:

- `phone_1`: newest FBNumber evidence;
- `phone_2`: strongest `fb_crawl` confidence, then newest evidence.

Confidence order for crawler evidence is `profile_field`, `strong_pattern`,
`weak_pattern`, then `unknown`. Stable IDs provide a final deterministic tie
breaker. The view is derived from evidence and is never written directly.

## Identity concurrency

A user may arrive with UID, username, profile URL, or a combination. Before
resolving the user row, the transaction acquires transaction-scoped PostgreSQL
advisory locks for every non-empty normalized identity alias. Alias keys are
sorted before locking so concurrent writers use a consistent lock order.

After locking:

1. query all rows matching any supplied alias using the unique indexes;
2. fail with a safe identity-conflict error if the aliases match multiple
   database users;
3. insert a new user or update the single resolved user;
4. use `INSERT ... ON CONFLICT` for normalized phones and evidence.

This prevents check-then-insert races while allowing a later crawl to enrich a
previously partial identity.

## Runtime data flow

```text
ScrapeResult
  -> direct crawler importer
  -> normalized UserBundle with phone_2 evidence
  -> FBNumber HTTP call outside database transaction
  -> source-aware merge adding phone_1 evidence
  -> one short PostgreSQL transaction per user
       -> lock identity aliases
       -> upsert user
       -> upsert normalized phones and evidence
       -> insert provider attempt
  -> commit
```

External HTTP calls never occur while database locks are held. Evidence rows
are written in a stable order to reduce deadlock risk. Reprocessing the same
user is idempotent except that repeated observation increments the evidence
count and adds a new provider-attempt audit row.

## Failure behavior

- `found`: persist user, all crawler/provider evidence, and the attempt.
- `not_found`: persist user, crawler evidence, and the completed attempt.
- `rate_limited` or `failed`: persist user, crawler evidence, and the safe
  failed attempt so a later job can retry.
- identity conflict: rollback the current user transaction and stop the run.
- PostgreSQL error: rollback the current user transaction and stop the run.
- earlier committed user transactions remain valid; rerunning is safe.

Artifact cleanup is a later step. It must not run unless every intended user
transaction completed successfully. Provider failure alone does not invalidate
the persisted crawler data, but it prevents destructive cleanup when a retry is
still required.

## Repository API

The initial repository exposes two explicit operations:

```text
apply_migrations() -> applied migration names
save_enriched_user(EnrichedUser) -> persisted user ID
```

The repository owns connections and transaction scope. Provider calls and
batch orchestration remain outside the repository. A connection factory is
injectable for unit tests and future pooling.

## Security

- `DATABASE_URL` and FBNumber credentials are environment/deployment secrets.
- No secret is accepted as a CLI argument.
- Errors exposed by the CLI do not include DSNs, tokens, SQL parameters, or raw
  provider bodies.
- The application role receives only the table and sequence privileges needed
  for select, insert, and update. It does not require superuser or schema-owner
  privileges during normal ingestion.
- Migration credentials may be separate from application credentials.

## Testing strategy

Implementation follows red-green-refactor:

1. migration contract tests fail before migration resources exist;
2. repository tests fail before migration and upsert behavior exists;
3. fake connection/cursor tests verify transaction order, safe parameters, and
   rollback behavior without requiring a live server;
4. PostgreSQL integration tests run when `TEST_DATABASE_URL` is explicitly set
   and otherwise skip;
5. the full existing `fb-crawl` suite must remain green.

Live integration checks cover migration idempotency, view slot selection,
identity updates, evidence deduplication, and provider-failure persistence.

## Deferred scope

- automatic artifact deletion after successful persistence;
- durable retry scheduling and cooldown policies for provider failures;
- job API and WebUI;
- data-retention/deletion workflows;
- database backup and restore automation.

