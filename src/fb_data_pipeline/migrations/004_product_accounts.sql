CREATE TABLE accounts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    normalized_email text NOT NULL,
    display_email text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL DEFAULT 'user',
    status text NOT NULL DEFAULT 'pending',
    email_verified_at timestamptz,
    deletion_requested_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (normalized_email),
    CHECK (normalized_email = lower(normalized_email)),
    CHECK (role IN ('user', 'admin')),
    CHECK (status IN ('pending', 'active', 'suspended', 'deleted')),
    CHECK (
        (status = 'deleted' AND deletion_requested_at IS NOT NULL)
        OR (status <> 'deleted' AND deletion_requested_at IS NULL)
    )
);

CREATE TABLE account_tokens (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    purpose text NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT account_tokens_token_hash_key UNIQUE (token_hash),
    CHECK (purpose IN ('email_verify', 'password_reset')),
    CHECK (expires_at > created_at),
    CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

CREATE INDEX account_tokens_account_id_idx
    ON account_tokens (account_id);
CREATE INDEX account_tokens_expires_at_idx
    ON account_tokens (expires_at)
    WHERE consumed_at IS NULL;

CREATE TABLE devices (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    installation_id uuid NOT NULL,
    display_name text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, installation_id),
    CHECK (status IN ('active', 'revoked')),
    CHECK (last_seen_at >= first_seen_at)
);

CREATE INDEX devices_account_id_status_idx
    ON devices (account_id, status, first_seen_at, id);

CREATE TABLE auth_sessions (
    id uuid PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    device_id bigint NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    refresh_token_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    rotated_from_id uuid REFERENCES auth_sessions (id) ON DELETE SET NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT auth_sessions_refresh_token_hash_key UNIQUE (refresh_token_hash),
    CONSTRAINT auth_sessions_rotated_from_id_key UNIQUE (rotated_from_id),
    CHECK (expires_at > created_at),
    CHECK (last_used_at >= created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX auth_sessions_account_id_idx
    ON auth_sessions (account_id);
CREATE INDEX auth_sessions_device_id_idx
    ON auth_sessions (device_id);
CREATE INDEX auth_sessions_expires_at_idx
    ON auth_sessions (expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE rate_limit_buckets (
    bucket_hash text NOT NULL,
    action text NOT NULL,
    window_start timestamptz NOT NULL,
    request_count integer NOT NULL DEFAULT 0,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (bucket_hash, action, window_start),
    CHECK (request_count >= 0),
    CHECK (expires_at > window_start)
);

CREATE INDEX rate_limit_buckets_expires_at_idx
    ON rate_limit_buckets (expires_at);
