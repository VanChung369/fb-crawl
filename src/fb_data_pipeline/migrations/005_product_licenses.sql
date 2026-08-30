CREATE TABLE plans (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    monthly_contact_limit integer NOT NULL,
    max_devices integer NOT NULL,
    allow_group_crawl boolean NOT NULL DEFAULT false,
    allow_comment_crawl boolean NOT NULL DEFAULT false,
    is_system boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (monthly_contact_limit >= 0),
    CHECK (max_devices >= 1)
);

INSERT INTO plans (
    code, name, monthly_contact_limit, max_devices,
    allow_group_crawl, allow_comment_crawl, is_system
) VALUES ('default', 'Default', 100, 1, false, false, true);

CREATE TABLE license_keys (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_digest text NOT NULL,
    key_version integer NOT NULL,
    masked_key text NOT NULL,
    duration_unit text NOT NULL,
    duration_value integer NOT NULL,
    monthly_contact_limit integer NOT NULL,
    max_devices integer NOT NULL,
    allow_group_crawl boolean NOT NULL DEFAULT false,
    allow_comment_crawl boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'available',
    created_by_account_id bigint REFERENCES accounts (id) ON DELETE SET NULL,
    redeemed_by_account_id bigint REFERENCES accounts (id) ON DELETE SET NULL,
    redeemed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    CHECK (key_version >= 1),
    CHECK (duration_unit IN ('day', 'month')),
    CHECK (duration_value > 0),
    CHECK (monthly_contact_limit >= 0),
    CHECK (max_devices >= 1),
    CHECK (status IN ('available', 'redeemed', 'revoked')),
    CHECK (
        (status = 'available' AND redeemed_by_account_id IS NULL AND redeemed_at IS NULL)
        OR (status = 'redeemed' AND redeemed_at IS NOT NULL)
        OR status = 'revoked'
    ),
    CHECK (revoked_at IS NULL OR status = 'revoked')
);

CREATE UNIQUE INDEX license_keys_key_digest_idx
    ON license_keys (key_version, key_digest);
CREATE INDEX license_keys_status_created_idx
    ON license_keys (status, created_at DESC, id DESC);

CREATE TABLE account_subscriptions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    license_key_id bigint NOT NULL UNIQUE REFERENCES license_keys (id),
    duration_unit text NOT NULL,
    duration_value integer NOT NULL,
    monthly_contact_limit integer NOT NULL,
    max_devices integer NOT NULL,
    allow_group_crawl boolean NOT NULL,
    allow_comment_crawl boolean NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'valid',
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (duration_unit IN ('day', 'month')),
    CHECK (duration_value > 0),
    CHECK (monthly_contact_limit >= 0),
    CHECK (max_devices >= 1),
    CHECK (ends_at > starts_at),
    CHECK (status IN ('valid', 'revoked')),
    CHECK (revoked_at IS NULL OR status = 'revoked')
);

CREATE INDEX account_subscriptions_account_time_idx
    ON account_subscriptions (account_id, starts_at, ends_at, id)
    WHERE status = 'valid';

CREATE TABLE usage_monthly (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    period_start date NOT NULL,
    used_contact_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, period_start),
    CHECK (used_contact_count >= 0)
);

CREATE INDEX usage_monthly_period_idx
    ON usage_monthly (period_start, account_id);

CREATE TABLE account_contact_reveals (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    facebook_user_id bigint NOT NULL REFERENCES facebook_users (id),
    phone_number_id bigint NOT NULL REFERENCES phone_numbers (id),
    lookup_event_id bigint,
    period_start date NOT NULL,
    revealed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, facebook_user_id, period_start)
);

CREATE INDEX account_contact_reveals_account_time_idx
    ON account_contact_reveals (account_id, revealed_at DESC, id DESC);
CREATE INDEX account_contact_reveals_facebook_user_idx
    ON account_contact_reveals (facebook_user_id);

CREATE TABLE admin_audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_account_id bigint REFERENCES accounts (id) ON DELETE SET NULL,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX admin_audit_events_actor_time_idx
    ON admin_audit_events (actor_account_id, created_at DESC, id DESC);
CREATE INDEX admin_audit_events_target_idx
    ON admin_audit_events (target_type, target_id, created_at DESC);
