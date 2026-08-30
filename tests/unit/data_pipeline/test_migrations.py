from fb_data_pipeline.migrations import load_migrations


def test_schema_migrations_are_packaged_with_stable_checksums() -> None:
    migrations = load_migrations()

    assert [item.version for item in migrations] == [
        "001_initial",
        "002_profile_attributes",
        "003_job_orchestration",
        "004_product_accounts",
        "005_product_licenses",
        "006_auth_session_reauthentication",
        "007_product_contact_lookup",
    ]
    assert all(len(item.checksum) == 64 for item in migrations)
    assert "CREATE TABLE facebook_users" in migrations[0].sql
    assert "CREATE TABLE phone_numbers" in migrations[0].sql
    assert "CREATE TABLE user_phone_evidence" in migrations[0].sql
    assert "CREATE TABLE enrichment_attempts" in migrations[0].sql
    assert "CREATE VIEW facebook_user_phone_slots" in migrations[0].sql
    assert "CREATE TABLE facebook_user_profiles" in migrations[1].sql
    assert "CREATE OR REPLACE VIEW facebook_user_phone_slots" in (
        migrations[1].sql
    )
    assert "CREATE TABLE crawl_jobs" in migrations[2].sql
    assert "CREATE TABLE crawl_targets" in migrations[2].sql
    assert "CREATE TABLE crawl_job_events" in migrations[2].sql
    assert "CREATE TABLE crawler_account_state" in migrations[2].sql
    assert "crawl_jobs_one_active_authenticated_account_idx" in (
        migrations[2].sql
    )
    assert "crawl_job_events_job_id_idx" in migrations[2].sql
    assert "facebook_users_updated_cursor_idx" in migrations[2].sql
    assert "facebook_users_username_prefix_idx" in migrations[2].sql
    assert "facebook_users_display_name_prefix_idx" in migrations[2].sql
    assert "VALUES ('default', 'ready')" in migrations[2].sql


def test_product_account_migration_is_fourth_and_isolated_from_shared_identity() -> None:
    migration = load_migrations()[3]

    assert migration.version == "004_product_accounts"
    assert "CREATE TABLE accounts" in migration.sql
    assert "CREATE TABLE account_tokens" in migration.sql
    assert "CREATE TABLE devices" in migration.sql
    assert "CREATE TABLE auth_sessions" in migration.sql
    assert "CREATE TABLE rate_limit_buckets" in migration.sql
    assert "UNIQUE (normalized_email)" in migration.sql
    assert "UNIQUE (account_id, installation_id)" in migration.sql
    assert "purpose IN ('email_verify', 'password_reset')" in migration.sql
    assert "role IN ('user', 'admin')" in migration.sql
    assert "status IN ('pending', 'active', 'suspended', 'deleted')" in migration.sql
    assert "auth_sessions_refresh_token_hash_key" in migration.sql
    assert "account_tokens_expires_at_idx" in migration.sql
    assert "auth_sessions_expires_at_idx" in migration.sql
    assert "facebook_users" not in migration.sql


def test_license_migration_seeds_default_plan_and_unique_reveal_ledger() -> None:
    migration = next(
        item for item in load_migrations() if item.version == "005_product_licenses"
    )

    assert migration.version == "005_product_licenses"
    for table in (
        "plans",
        "license_keys",
        "account_subscriptions",
        "usage_monthly",
        "account_contact_reveals",
        "admin_audit_events",
    ):
        assert f"CREATE TABLE {table}" in migration.sql
    assert (
        "VALUES ('default', 'Default', 100, 1, false, false, true)"
        in migration.sql
    )
    assert "license_keys_key_digest_idx" in migration.sql
    assert "account_subscriptions_account_time_idx" in migration.sql
    assert "UNIQUE (account_id, period_start)" in migration.sql
    assert "UNIQUE (account_id, facebook_user_id, period_start)" in migration.sql
    assert "lookup_event_id bigint" in migration.sql
    assert "REFERENCES lookup_events" not in migration.sql
    assert "monthly_contact_limit >= 0" in migration.sql
    assert "max_devices >= 1" in migration.sql


def test_reauthentication_migration_backfills_refresh_chains_from_root_login() -> None:
    migration = next(
        item
        for item in load_migrations()
        if item.version == "006_auth_session_reauthentication"
    )

    assert "WITH RECURSIVE session_roots" in migration.sql
    assert "root_session.created_at" in migration.sql


def test_contact_lookup_migration_adds_single_flight_history_and_exports() -> None:
    migration = next(
        item
        for item in load_migrations()
        if item.version == "007_product_contact_lookup"
    )

    for table in (
        "provider_lookup_state",
        "enrichment_leases",
        "lookup_events",
        "export_jobs",
    ):
        assert f"CREATE TABLE {table}" in migration.sql
    assert "PRIMARY KEY (facebook_user_id, provider, field)" in migration.sql
    assert "account_contact_reveals_lookup_event_fk" in migration.sql
    assert "REFERENCES lookup_events (id) ON DELETE SET NULL" in migration.sql
    assert "lookup_events_account_time_idx" in migration.sql
    assert "lookup_events_device_idx" in migration.sql
    assert "lookup_events_facebook_user_idx" in migration.sql
    assert "provider_lookup_state_attempt_idx" in migration.sql
    assert "account_contact_reveals_lookup_event_idx" in migration.sql
    assert "export_jobs_account_status_created_idx" in migration.sql
    assert "found', 'not_found', 'processing', 'quota_exceeded', 'failed" in migration.sql
    assert "queued', 'running', 'completed', 'failed', 'expired" in migration.sql


def test_migrations_are_sorted_by_version() -> None:
    migrations = load_migrations()

    assert tuple(item.version for item in migrations) == tuple(
        sorted(item.version for item in migrations)
    )
