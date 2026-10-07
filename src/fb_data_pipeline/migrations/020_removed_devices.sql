-- Keep revoked installation identities and their audit/session references.
ALTER TABLE devices ADD COLUMN deleted_at timestamptz;
ALTER TABLE devices ADD CONSTRAINT deleted_devices_are_revoked
    CHECK (deleted_at IS NULL OR status = 'revoked');
