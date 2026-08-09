# Authenticated Persistence Coverage Design

Date: 2026-08-09

## Purpose

Complete direct PostgreSQL persistence coverage for authenticated commands that
produce Facebook user records. Add the existing `--persist` and
`--keep-output` contract to `profile`, `engagement`, and `batch` without
changing the database schema, provider contract, or legacy export behavior.

After this phase, all authenticated user-producing collection actions support
the same in-memory FBNumber and PostgreSQL flow. `messages`, `inspect`, and
`repair` remain outside persistence because they do not produce a regular
`ScrapeResult[UserRecord]` suitable for this pipeline.

## Command contract

The following additional commands accept persistence flags:

```powershell
fb-crawl authenticated profile <PROFILE_URL> --persist
fb-crawl authenticated engagement <POST_URL> --persist
fb-crawl authenticated batch --input runtime/targets.txt --persist
```

The established rules remain unchanged:

- `--persist` sends typed in-memory user results through FBNumber and
  PostgreSQL and does not create compatibility output;
- `--persist --keep-output` writes normal output before provider/database work;
- explicit `--output` with `--persist` requires `--keep-output`;
- `--keep-output` without `--persist` is invalid;
- cache, session, checkpoint, target, and existing output files are retained.

## Result routing

`profile` and `engagement` return `ScrapeResult[UserRecord]`, so they are passed
unchanged to `AuthenticatedPersistenceRuntime.ingest_result`.

`batch` returns `AuthenticatedBatchResult`. Its `user_result` contains the
deduplicated `UserRecord` values collected by profile, members, comments,
friends, followers, reactions, and engagement sub-actions. Only this
`user_result` is passed to the ingestion service. `message_result` and
`inspect_result` remain available only through compatibility output and are
never coerced into Facebook users.

A small typed routing helper in the authenticated CLI owns this distinction:

```text
pipeline_user_result(action, result)
  profile/engagement/other user action -> result
  batch                                -> result.user_result
```

The ingestion service and repository remain unaware of batch envelopes.

## Empty and mixed batches

A batch containing only message and/or inspect targets has an empty
`user_result`. The normal ingestion service accepts that empty typed result and
returns zero pipeline users. Consequently it performs no FBNumber requests and
no PostgreSQL user transactions.

A mixed batch persists only its user records. With `--keep-output`, the normal
batch exporter still writes user, message, and inspect artifacts exactly as it
does without persistence. Provider or database failure after export does not
remove those artifacts.

## Failure behavior

The existing failure policy is reused without modification:

- provider `not_found` is successful;
- provider `failed` or `rate_limited` persists Facebook data and returns exit
  code `1` for required retry;
- individual identity conflicts continue through remaining users and yield
  exit code `5`;
- database connection/driver failure stops the batch with exit code `5`;
- interrupted Facebook collection has exit code `130` after collected users
  are processed;
- provider and browser resources close independently on every path.

Batch-level message or inspect issues continue to contribute to the existing
crawl result exit status. They do not appear as provider/database failures.

## Data and schema

No new tables, columns, migrations, or writable phone slots are introduced.
Profile values and phone evidence follow the existing contract:

- FBNumber evidence derives `phone_1`;
- Facebook-visible evidence derives `phone_2`;
- profile `address`, `birth_date`, and `gender` remain trimmed raw text;
- PostgreSQL `facebook_user_phone_slots` remains the authoritative read view.

## Testing

Implementation follows red-green-refactor and covers:

1. parser acceptance for `profile`, `engagement`, and `batch`;
2. continued parser rejection for `messages`, `inspect`, and `repair`;
3. profile and engagement pass the exact original `ScrapeResult` to ingestion;
4. batch passes the exact `AuthenticatedBatchResult.user_result` object;
5. message/inspect-only batch reports zero persisted users without provider or
   repository activity;
6. default persistence performs no export;
7. `--keep-output` preserves normal profile, engagement, and batch exports
   before ingestion;
8. existing summaries, exit precedence, resource closure, and legacy commands
   remain unchanged;
9. the full unit and PostgreSQL integration suites remain green.

## Deferred scope

- direct PostgreSQL persistence for message or inspect records;
- persistence for CSV-based `repair` input;
- durable FBNumber retry workers and cooldown scheduling;
- job tables, API endpoints, and WebUI;
- public crawl persistence orchestration.
