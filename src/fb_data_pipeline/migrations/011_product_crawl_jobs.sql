CREATE TABLE product_crawl_jobs (
    id uuid PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    scope text NOT NULL,
    target_url text NOT NULL,
    max_identities integer NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    discovered_count integer NOT NULL DEFAULT 0,
    processed_count integer NOT NULL DEFAULT 0,
    found_count integer NOT NULL DEFAULT 0,
    not_found_count integer NOT NULL DEFAULT 0,
    quota_exceeded_count integer NOT NULL DEFAULT 0,
    safe_error_code text NOT NULL DEFAULT '',
    cancel_requested_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz,
    CHECK (scope IN ('members', 'engagement', 'both')),
    CHECK (status IN (
        'queued', 'running', 'succeeded', 'partial', 'failed',
        'cancelled', 'blocked'
    )),
    CHECK (max_identities BETWEEN 1 AND 1000),
    CHECK (
        discovered_count >= 0 AND processed_count >= 0
        AND found_count >= 0 AND not_found_count >= 0
        AND quota_exceeded_count >= 0
    )
);

CREATE INDEX product_crawl_jobs_account_created_idx
    ON product_crawl_jobs (account_id, created_at DESC, id DESC);

CREATE TABLE product_crawl_job_children (
    product_crawl_job_id uuid NOT NULL
        REFERENCES product_crawl_jobs (id) ON DELETE CASCADE,
    crawl_job_id uuid NOT NULL REFERENCES crawl_jobs (id) ON DELETE CASCADE,
    action text NOT NULL CHECK (action IN ('members', 'comments')),
    PRIMARY KEY (product_crawl_job_id, crawl_job_id)
);

CREATE UNIQUE INDEX product_crawl_job_children_crawl_job_idx
    ON product_crawl_job_children (crawl_job_id);

ALTER TABLE lookup_events
    ADD CONSTRAINT lookup_events_product_crawl_job_id_fkey
    FOREIGN KEY (product_crawl_job_id)
    REFERENCES product_crawl_jobs (id) ON DELETE SET NULL;
