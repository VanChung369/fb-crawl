from fb_data_pipeline.migrations import load_migrations


def test_schema_migrations_are_packaged_with_stable_checksums() -> None:
    migrations = load_migrations()

    assert [item.version for item in migrations] == [
        "001_initial",
        "002_profile_attributes",
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


def test_migrations_are_sorted_by_version() -> None:
    migrations = load_migrations()

    assert tuple(item.version for item in migrations) == tuple(
        sorted(item.version for item in migrations)
    )
