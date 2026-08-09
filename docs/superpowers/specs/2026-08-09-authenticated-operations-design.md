# Authenticated Operations Design

## Goal

Complete the useful CLI layer before database/API/WebUI work:

- resumable and incremental collection using repository-ignored runtime files;
- profile enrichment v2 with field status, source, and timestamps;
- combined post engagement collection;
- typed universal authenticated batch input;
- safe selector/session diagnostics through `inspect`.

SQLite, PostgreSQL persistence, external phone APIs, WebUI, and job APIs are
explicitly deferred.

## Future data flow

The future application direction is:

```text
Facebook collectors
  -> normalized records
  -> enrichment provider pipeline
       -> optional external phone providers
  -> merge with provenance/conflict policy
  -> PostgreSQL repository
  -> job API / WebUI
```

This phase must not call `api.fbnumber.com` or any other external enrichment
service. It only defines record provenance in a way that a later provider can
append values without overwriting crawl evidence.

## Enrichment v2

Add visible fields `bio`, `workplace`, `education`, `gender`, `languages`, and
`relationship_status`. Continue supporting phone, website, address, current
city, hometown, and birth date/year.

Each requested field has one status:

- `found`: a normalized visible value was parsed;
- `not_visible`: the relevant rendered route loaded but no value was visible;
- `section_unavailable`: Facebook did not render the requested section within
  the bounded wait;
- `navigation_failed`: the route could not be loaded safely;
- `not_requested`: the field was outside the requested set or profile limit.

Records expose semicolon-separated `field_status` and `field_sources` in flat
formats plus `first_seen`, `last_seen`, and `last_enriched_at` ISO-8601 UTC
timestamps. Field sources use stable names such as
`facebook:directory_personal_details`, `facebook:directory_work`, and
`facebook:directory_links`.

## Engagement

`authenticated engagement POST_URL` performs the existing visible comment and
reaction passes for each post. Records are globally deduplicated and expose
`commented`, `reacted`, `reaction_types`, and `interaction_count`.

Reaction types are populated only when a visible accessible label is associated
with the rendered reaction row. Unknown reaction types stay empty.

## Universal batch

Batch lines may remain untyped for backward compatibility or use:

```text
members:URL
comments:URL
profile:URL
friends:URL
followers:URL
reactions:URL
engagement:URL
messages:URL
inspect:URL
```

Untyped group URLs remain `members`; untyped supported posts remain `comments`;
explicit friends/followers/messages routes retain their action; plain profile
URLs become `profile`.

Mixed user/message/diagnostic batches are split into type-specific output files
by the CLI exporter. A target failure is isolated; session loss remains fatal.

## Runtime checkpoint

Checkpoint files are JSON under `runtime/checkpoints/` by default and are
written atomically. They contain normalized record data, completed targets,
known identities, action, targets, and a schema version. They never contain
cookies, passwords, full HTML, screenshots, or browser storage.

- `--resume` restores records and skips completed normalized targets for a
  matching action/target set.
- `--incremental` re-runs targets, emits only identities not known in the
  checkpoint, and updates the known identity set.
- A mismatched/corrupt checkpoint fails safely before browser startup.

Resume granularity is target-level. Browser loops remain bounded and never
attempt to reconstruct a private Facebook cursor.

## Inspect

`authenticated inspect URL` navigates one supported explicit target and emits
only sanitized diagnostics: session validity, document readiness, presence of
main/dialog regions, counts of visible profile links/message rows/profile
labels, normalized target, and parser version. No text content or raw DOM is
written.

## Security

- Only data rendered to the authenticated operator is parsed.
- No privacy, CAPTCHA, checkpoint, or 2FA bypass.
- Message checkpoints/exports are sensitive and remain under ignored runtime.
- Future provider credentials must use secret configuration, never CLI
  arguments, logs, checkpoint files, or committed configuration.
