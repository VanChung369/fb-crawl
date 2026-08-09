# Future External Enrichment and PostgreSQL Direction

This document records the planned direction only. The current CLI does not call
external phone APIs and does not connect to PostgreSQL.

The local normalization/deduplication quality gate is now available through
`fb-crawl data merge`; external providers and PostgreSQL remain future work.

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

An external provider such as `api.fbnumber.com` may later receive a normalized
subset such as `username`, `name`, and `uid`. Provider calls must be explicit,
rate-limited, timeout-bounded, retry-bounded, and authorized under the
provider's terms and applicable privacy rules.

## Provider boundary

The future application should depend on an internal interface rather than an
endpoint-specific response:

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

## PostgreSQL direction

Likely entities:

- `crawl_jobs` and `crawl_targets`;
- `facebook_users` and `facebook_profiles`;
- `profile_field_values` with source/status/timestamps;
- `phone_numbers` and `user_phone_evidence`;
- `engagements` and source objects;
- `messages` in a separately permissioned/retained area;
- `enrichment_attempts` and safe provider diagnostics.

Use unique constraints for normalized Facebook identity and normalized phone
values, transactional upserts, source-aware merge rules, and retention/deletion
policies. Provider API keys belong in a secret manager or deployment secret,
never CLI arguments, checkpoints, output files, logs, or committed `.env`
files.
