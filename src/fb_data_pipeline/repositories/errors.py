from fb_crawl.core.exceptions import FbCrawlError


class DatabaseError(FbCrawlError):
    code = "database_error"
    exit_code = 5


class DatabaseIdentityConflict(DatabaseError):
    code = "database_identity_conflict"


class MigrationChecksumError(DatabaseError):
    code = "database_migration_checksum_mismatch"
