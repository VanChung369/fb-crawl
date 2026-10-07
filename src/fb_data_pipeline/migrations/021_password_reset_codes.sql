CREATE TABLE password_reset_codes (
    account_id bigint PRIMARY KEY REFERENCES accounts (id) ON DELETE CASCADE,
    code_hash text NOT NULL,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);
