# Subscription quota validation — 2026-10-17

## Verified behavior

Real PostgreSQL 17 tests reproduced the 479-contact carryover before the fix.
The new expiry, new-key, default-to-paid-to-default and queued-key tests all
failed with the expected old counter, then passed with scoped quota storage.

Final focused backend run: **69 passed**. Command:

```powershell
python -m pytest tests/integration/data_pipeline/test_contact_quota.py tests/integration/data_pipeline/test_license_repository.py tests/integration/data_pipeline/test_history_isolation.py tests/unit/entitlements tests/unit/licenses tests/unit/api/test_license_routes.py tests/unit/data_pipeline/test_migrations.py -q
```

This includes concurrent reservations for the last slot, real SQL backfill of
400 old-key + 79 current-key + 3 free contacts, and a second migration case
with 100 deleted paid contacts retained in the reconciliation archive.

Focused extension run: **37 passed**, covering license UI, saved scan counters,
contact/history UI and entitlement cache behavior. Late old-key lookup
responses are tested for renewal, expiry and month rollover. TypeScript
compilation and the production Chrome extension build pass. The build retains
the existing warning about a large ExcelJS chunk.

An independent code review checked migration, query isolation, reservation
concurrency, scheduling, and UI usage propagation. Its two findings (deleted
reveal attribution and cached scan counters) were corrected and re-reviewed.

## Broader suite findings

The full suites were run as well; they are not entirely green. No production
database was migrated. PostgreSQL tests used a separate disposable container.

Backend full-suite snapshot: **1,523 passed, 39 skipped, 16 setup errors**.
All 16 errors are in `tests/integration/data_pipeline/test_postgres_repository.py`:
its existing cleanup tries to truncate `enrichment_attempts` while
`provider_lookup_state` references it, without truncating the referencing
table or using CASCADE. Affected tests:

- `test_migrations_are_idempotent_on_postgresql`
- `test_reprocessing_user_deduplicates_data_and_counts_evidence`
- `test_later_aliases_enrich_the_same_user`
- `test_conflicting_aliases_roll_back_current_user`
- `test_view_selects_newest_provider_and_strongest_crawler_phone`
- `test_provider_failure_statuses_keep_crawler_phone[not_found]`
- `test_provider_failure_statuses_keep_crawler_phone[rate_limited]`
- `test_provider_failure_statuses_keep_crawler_phone[failed]`
- `test_profile_view_prefers_newer_values_and_preserves_blank_fields`
- `test_deleting_user_cascades_to_profile_snapshot`
- `test_in_memory_crawl_enrichment_persists_authoritative_view`
- `test_latest_terminal_attempt_suppresses_older_retryable_attempt`
- `test_retry_candidate_selection_honors_cooldown_force_and_identity`
- `test_fbnumber_retry_advisory_lock_serializes_workers`
- `test_successful_retry_persists_phone_and_preserves_crawler_data`
- `test_repeated_failure_starts_a_new_cooldown`

Frontend full-suite snapshot: **781 passed, 2 failed**:

- `SessionCoordinator > stores new rows during a permanent summary pause and uploads them only after explicit resume`
  in `tests/unit/interaction-session-coordinator.test.ts`: also fails in a
  serial focused rerun. It expects a paused summary/upload but receives saved
  state; the coordinator implementation is unchanged by this fix.
- `exports every page to XLSX without merging independent comments from the same author`
  in `tests/unit/session-history.test.tsx`: timed out in the parallel full run;
  passed when this file was rerun with one worker.

The serial rerun of those two files plus license tests had **74 passed, 1 failed**
(the coordinator test above). These unrelated failures were not changed as
part of the quota fix.
