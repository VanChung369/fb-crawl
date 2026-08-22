CREATE TABLE crawl_jobs (
    id uuid PRIMARY KEY,
    mode text NOT NULL,
    action text NOT NULL,
    account_key text NOT NULL DEFAULT 'default',
    status text NOT NULL,
    request_options jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text,
    request_fingerprint text NOT NULL,
    retry_of_job_id uuid REFERENCES crawl_jobs (id) ON DELETE SET NULL,
    priority smallint NOT NULL DEFAULT 0,
    attempt integer NOT NULL DEFAULT 0,
    worker_id text,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    cancel_requested_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    requested_targets integer NOT NULL DEFAULT 0,
    completed_targets integer NOT NULL DEFAULT 0,
    failed_targets integer NOT NULL DEFAULT 0,
    discovered_users integer NOT NULL DEFAULT 0,
    persisted_users integer NOT NULL DEFAULT 0,
    provider_retries_required integer NOT NULL DEFAULT 0,
    current_target_id uuid,
    error_code text NOT NULL DEFAULT '',
    error_message text NOT NULL DEFAULT '',
    CHECK (mode = 'authenticated'),
    CHECK (action IN (
        'members', 'comments', 'profile', 'friends', 'followers',
        'reactions', 'engagement'
    )),
    CHECK (status IN (
        'queued', 'running', 'cancelling', 'succeeded', 'partial',
        'failed', 'cancelled', 'blocked'
    )),
    CHECK (
        requested_targets >= 0 AND completed_targets >= 0
        AND failed_targets >= 0 AND discovered_users >= 0
        AND persisted_users >= 0 AND provider_retries_required >= 0
    )
);

CREATE TABLE crawl_targets (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES crawl_jobs (id) ON DELETE CASCADE,
    target_key text NOT NULL,
    target_url text NOT NULL,
    target_kind text NOT NULL,
    position integer NOT NULL,
    status text NOT NULL,
    attempt integer NOT NULL DEFAULT 0,
    checkpoint_path text NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    steps_completed integer NOT NULL DEFAULT 0,
    items_discovered integer NOT NULL DEFAULT 0,
    users_persisted integer NOT NULL DEFAULT 0,
    provider_retries_required integer NOT NULL DEFAULT 0,
    error_code text NOT NULL DEFAULT '',
    error_message text NOT NULL DEFAULT '',
    CHECK (status IN (
        'pending', 'running', 'succeeded', 'partial', 'failed', 'skipped',
        'cancelled', 'blocked'
    )),
    CHECK (
        position >= 0 AND attempt >= 0 AND steps_completed >= 0
        AND items_discovered >= 0 AND users_persisted >= 0
        AND provider_retries_required >= 0
    ),
    UNIQUE (job_id, target_key)
);

ALTER TABLE crawl_jobs
    ADD CONSTRAINT crawl_jobs_current_target_id_fkey
    FOREIGN KEY (current_target_id) REFERENCES crawl_targets (id)
    ON DELETE SET NULL;

CREATE TABLE crawl_job_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES crawl_jobs (id) ON DELETE CASCADE,
    target_id uuid REFERENCES crawl_targets (id) ON DELETE CASCADE,
    event_type text NOT NULL,
    level text NOT NULL,
    safe_message text NOT NULL DEFAULT '',
    counters jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (level IN ('debug', 'info', 'warning', 'error'))
);

CREATE TABLE crawler_account_state (
    account_key text PRIMARY KEY,
    status text NOT NULL DEFAULT 'ready',
    cooldown_until timestamptz,
    last_job_id uuid REFERENCES crawl_jobs (id) ON DELETE SET NULL,
    last_started_at timestamptz,
    last_finished_at timestamptz,
    rate_limit_count_24h integer NOT NULL DEFAULT 0,
    last_rate_limit_at timestamptz,
    last_warning_code text NOT NULL DEFAULT '',
    last_warning_at timestamptz,
    block_reason text NOT NULL DEFAULT '',
    acknowledged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('ready', 'cooldown', 'blocked', 'manual_review')),
    CHECK (rate_limit_count_24h >= 0)
);

INSERT INTO crawler_account_state (account_key, status)
VALUES ('default', 'ready')
ON CONFLICT (account_key) DO NOTHING;

CREATE UNIQUE INDEX crawl_jobs_idempotency_key_idx
    ON crawl_jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX crawl_jobs_one_retry_child_idx
    ON crawl_jobs (retry_of_job_id)
    WHERE retry_of_job_id IS NOT NULL;

CREATE UNIQUE INDEX crawl_jobs_one_active_authenticated_account_idx
    ON crawl_jobs (account_key)
    WHERE mode = 'authenticated' AND status IN ('running', 'cancelling');

CREATE INDEX crawl_jobs_claim_idx
    ON crawl_jobs (priority DESC, created_at, id)
    WHERE status = 'queued';

CREATE INDEX crawl_targets_job_position_idx
    ON crawl_targets (job_id, position, id);

CREATE INDEX crawl_job_events_job_id_idx
    ON crawl_job_events (job_id, id);

CREATE INDEX facebook_users_updated_cursor_idx
    ON facebook_users (updated_at DESC, id DESC);

CREATE INDEX facebook_users_username_prefix_idx
    ON facebook_users (normalized_username text_pattern_ops);

CREATE INDEX facebook_users_display_name_prefix_idx
    ON facebook_users ((lower(display_name)) text_pattern_ops);
