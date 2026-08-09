# Profile Attributes Persistence Design

Date: 2026-08-09

## Purpose

Preserve the Facebook-visible `address`, `birth_date`, and `gender` fields when
crawler records pass through `fb_data_pipeline` and are persisted to
PostgreSQL. Values remain raw display text because Facebook may expose a full
date, a partial date, only a year, localized text, or a free-form gender label.

## Confirmed product rules

- Persist exactly `address`, `birth_date`, and `gender` in this slice.
- Trim surrounding whitespace but otherwise preserve Facebook-visible text.
- Do not parse `birth_date` into a PostgreSQL `date`.
- Empty incoming values never erase stored non-empty values.
- A newer observed snapshot may replace an older non-empty value.
- Profile attributes remain separate from Facebook identity aliases.
- Existing phone rules are unchanged: FBNumber remains `phone_1` and crawler
  evidence remains `phone_2`.
- Do not modify the already-applied `001_initial.sql` migration.
- Do not commit, merge, push, or delete runtime artifacts.

## Typed pipeline model

Add an immutable `ProfileData` value to `fb_data_pipeline.core.models`:

```text
ProfileData
  address: str
  birth_date: str
  gender: str
  source_url: str
  observed_at: datetime | None
```

Construction trims every string. `is_empty` is true when all three profile
fields are empty. `source_url` and `observed_at` describe the snapshot source;
they are not identity aliases.

`UserBundle` gains `profile: ProfileData` with an empty default. This keeps
existing provider and test construction backward-compatible.

## Import and merge behavior

`import_user_record` copies all three fields from `fb_crawl.UserRecord`.
`source_url` uses the record source URL and falls back to the canonical profile
URL. `observed_at` uses the first parseable value from `last_enriched_at`,
`last_seen`, and `first_seen`.

`import_page_record` copies the page address. Page records do not currently
expose birth date or gender, so those values remain empty. Its canonical URL is
the snapshot source and its observation time remains absent.

Duplicate bundles merge profile snapshots field by field:

1. an empty incoming value never wins;
2. an incoming value fills an empty existing field;
3. when both observations have timestamps, the newer value wins;
4. a timestamped value wins over an otherwise conflicting untimestamped value;
5. when both observations lack timestamps, the first non-empty value remains
   for deterministic results.

The merged snapshot timestamp is the newest known timestamp. Its source URL is
the source of the snapshot whose values were accepted most recently.

`EnrichmentPipeline` must copy `original.profile` when it combines crawler and
provider phone evidence. FBNumber never creates or changes these Facebook
profile attributes.

## PostgreSQL migration

Add a new packaged migration named `002_profile_attributes.sql`. It creates:

```text
facebook_user_profiles
  facebook_user_id bigint primary key
  address text nullable
  birth_date text nullable
  gender text nullable
  source_url text not null default ''
  observed_at timestamptz nullable
  created_at timestamptz not null default now()
  updated_at timestamptz not null default now()
```

`facebook_user_id` references `facebook_users(id)` with `ON DELETE CASCADE`.
A check constraint requires at least one non-blank profile field. The primary
key provides the foreign-key index and enforces one current profile row per
Facebook user.

The migration runner discovers `001_initial` followed by
`002_profile_attributes`. The checksum of applied `001_initial` remains valid.

## Repository transaction

`PostgresRepository.save_enriched_user` keeps the existing short transaction:

```text
lock and upsert identity
  -> upsert non-empty profile snapshot
  -> upsert phone evidence
  -> insert provider attempt
  -> commit
```

The profile upsert converts blank fields to SQL `NULL`. On conflict it updates
each field independently using the merge rules above. A stale or untimestamped
snapshot cannot erase or replace a newer stored value. `observed_at` advances
to the newest known time, and `updated_at` changes only through an accepted
upsert. An empty `ProfileData` performs no profile SQL.

Any profile SQL error rolls back the complete current-user transaction,
including identity, phones, and provider attempt. Earlier user transactions
remain committed.

## Read behavior

Extend `facebook_user_phone_slots` through the new migration using
`CREATE OR REPLACE VIEW`. The view continues to expose all existing columns and
adds nullable `address`, `birth_date`, and `gender` through a left join to
`facebook_user_profiles`. Existing callers selecting named columns remain
compatible.

## Testing

Implementation follows red-green-refactor:

1. model tests cover cleaning, defaults, and empty detection;
2. importer tests prove crawler fields reach `UserBundle.profile`;
3. merge tests cover newer, missing, and untimestamped values;
4. pipeline tests prove FBNumber enrichment preserves the profile snapshot;
5. migration tests require `001_initial` then `002_profile_attributes`;
6. fake repository tests verify profile SQL order, parameters, skip-empty, and
   rollback behavior;
7. PostgreSQL integration tests verify migration application, non-destructive
   upsert, stale-snapshot protection, and view output;
8. the complete test suite, compile check, dependency check, CLI smoke, and
   diff check must remain green.

## Deferred scope

- `current_city`, `hometown`, workplace, education, bio, and other profile
  fields;
- per-field history tables and per-field source URLs;
- canonical parsing of localized birth dates or gender labels;
- automatic crawl-to-provider-to-database CLI wiring;
- CSV or artifact deletion.
