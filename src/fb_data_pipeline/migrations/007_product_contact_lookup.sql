CREATE TABLE provider_lookup_state (
    facebook_user_id bigint NOT NULL REFERENCES facebook_users (id) ON DELETE CASCADE,
    provider text NOT NULL,
    field text NOT NULL,
    latest_status text NOT NULL,
    checked_at timestamptz NOT NULL,
    refresh_after timestamptz NOT NULL,
    latest_attempt_id bigint REFERENCES enrichment_attempts (id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (facebook_user_id, provider, field),
    CHECK (provider <> ''),
    CHECK (field <> ''),
    CHECK (latest_status IN ('found', 'not_found', 'failed', 'rate_limited')),
    CHECK (refresh_after >= checked_at),
    CHECK (updated_at >= checked_at)
);

CREATE INDEX provider_lookup_state_refresh_idx
    ON provider_lookup_state (provider, field, refresh_after, facebook_user_id);
CREATE INDEX provider_lookup_state_attempt_idx
    ON provider_lookup_state (latest_attempt_id)
    WHERE latest_attempt_id IS NOT NULL;

CREATE TABLE enrichment_leases (
    facebook_user_id bigint NOT NULL REFERENCES facebook_users (id) ON DELETE CASCADE,
    provider text NOT NULL,
    field text NOT NULL,
    owner_token text NOT NULL,
    leased_until timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (facebook_user_id, provider, field),
    CHECK (provider <> ''),
    CHECK (field <> ''),
    CHECK (owner_token <> ''),
    CHECK (leased_until > created_at),
    CHECK (updated_at >= created_at)
);

CREATE INDEX enrichment_leases_expiry_idx
    ON enrichment_leases (leased_until, facebook_user_id);

CREATE TABLE lookup_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    device_id bigint REFERENCES devices (id) ON DELETE SET NULL,
    facebook_user_id bigint NOT NULL REFERENCES facebook_users (id),
    requested_uid text,
    requested_username text,
    requested_profile_url text,
    outcome text NOT NULL DEFAULT 'processing',
    result_source text NOT NULL DEFAULT 'none',
    provider_called boolean NOT NULL DEFAULT false,
    quota_charged boolean NOT NULL DEFAULT false,
    safe_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK (requested_uid IS NOT NULL OR requested_username IS NOT NULL OR requested_profile_url IS NOT NULL),
    CHECK (outcome IN ('found', 'not_found', 'processing', 'quota_exceeded', 'failed')),
    CHECK (result_source IN ('cache', 'provider', 'negative_cache', 'none')),
    CHECK (
        (outcome = 'processing' AND completed_at IS NULL)
        OR (outcome <> 'processing' AND completed_at IS NOT NULL)
    ),
    CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE INDEX lookup_events_account_time_idx
    ON lookup_events (account_id, created_at DESC, id DESC);
CREATE INDEX lookup_events_account_user_idx
    ON lookup_events (account_id, facebook_user_id, created_at DESC, id DESC);
CREATE INDEX lookup_events_account_outcome_idx
    ON lookup_events (account_id, outcome, created_at DESC, id DESC);
CREATE INDEX lookup_events_device_idx
    ON lookup_events (device_id)
    WHERE device_id IS NOT NULL;
CREATE INDEX lookup_events_facebook_user_idx
    ON lookup_events (facebook_user_id);

CREATE TABLE export_jobs (
    id uuid PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    format text NOT NULL,
    filter_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'queued',
    owner_token text,
    leased_until timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    safe_error_code text,
    artifact_path text,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK (format IN ('csv', 'xlsx')),
    CHECK (status IN ('queued', 'running', 'completed', 'failed', 'expired')),
    CHECK (attempt_count >= 0),
    CHECK ((owner_token IS NULL) = (leased_until IS NULL)),
    CHECK (completed_at IS NULL OR completed_at >= created_at),
    CHECK (expires_at IS NULL OR expires_at > created_at),
    CHECK (updated_at >= created_at)
);

CREATE INDEX export_jobs_account_status_created_idx
    ON export_jobs (account_id, status, created_at DESC, id);
CREATE INDEX export_jobs_claim_idx
    ON export_jobs (status, created_at, id)
    WHERE status IN ('queued', 'running');
CREATE INDEX export_jobs_lease_idx
    ON export_jobs (leased_until, id)
    WHERE status = 'running';
CREATE INDEX export_jobs_expiry_idx
    ON export_jobs (expires_at, id)
    WHERE status = 'completed';

ALTER TABLE account_contact_reveals
    ADD CONSTRAINT account_contact_reveals_lookup_event_fk
    FOREIGN KEY (lookup_event_id)
    REFERENCES lookup_events (id) ON DELETE SET NULL;

CREATE INDEX account_contact_reveals_lookup_event_idx
    ON account_contact_reveals (lookup_event_id)
    WHERE lookup_event_id IS NOT NULL;
