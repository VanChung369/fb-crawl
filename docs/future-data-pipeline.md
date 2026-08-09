# External Enrichment and Future Pipeline Direction

The in-process pipeline now normalizes crawler results, calls the configured
FBNumber provider, merges source-aware phone evidence, and can persist it to
PostgreSQL through `PostgresRepository` and `PipelinePersistenceService`.
`fb-crawl pipeline migrate` applies the packaged schema. Existing crawl commands
are not yet automatically wired to persistence.

The local normalization/deduplication quality gate is now available through
`fb-crawl data merge`, and `fb-crawl data plan` produces bounded follow-up
profile batches from its master output. Authenticated phone extraction also
writes `*-phone-evidence.csv` with source URL, capture time, and confidence, so
provider values can be merged without erasing Facebook-visible provenance.
`fb-crawl data phone-merge` consolidates those files into a normalized,
quality-checked evidence master. Automatic job orchestration and cleanup remain
future work.

## Intended pipeline

```text
Facebook crawl
  -> normalize/deduplicate identity
  -> visible Facebook profile enrichment
  -> external enrichment providers
  -> merge values with provenance
  -> validate result
  -> PostgreSQL transaction
  -> job API / WebUI
```

An external provider such as `api.fbnumber.com` receives a normalized subset
such as `username`, `name`, and `uid`. Provider calls must be explicit,
rate-limited, timeout-bounded, retry-bounded, and authorized under the
provider's terms and applicable privacy rules.

## Provider boundary

The pipeline depends on an internal interface rather than an endpoint-specific
response:

```text
PhoneEnrichmentProvider.search(identity) -> ProviderEnrichmentResult
```

The normalized result should contain:

- provider name and request correlation ID;
- normalized phone values;
- per-value source and confidence when provided;
- provider status (`found`, `not_found`, `rate_limited`, `failed`);
- checked timestamp;
- a safe error code without credentials or raw response bodies.

Facebook-visible phone values and external-provider values must be unioned and
deduplicated. An external value must never silently overwrite crawl evidence.

## PostgreSQL foundation and future expansion

Implemented entities:

- `facebook_users`;
- `phone_numbers` and `user_phone_evidence`;
- `enrichment_attempts`;
- the derived `facebook_user_phone_slots` view.

Likely future entities:

- `crawl_jobs` and `crawl_targets`;
- `facebook_profiles`;
- `profile_field_values` with source/status/timestamps;
- `engagements` and source objects;
- `messages` in a separately permissioned/retained area.

Use unique constraints for normalized Facebook identity and normalized phone
values, transactional upserts, source-aware merge rules, and retention/deletion
policies. Provider API keys belong in a secret manager or deployment secret,
never CLI arguments, checkpoints, output files, logs, or committed `.env`
files.
