ALTER TABLE lookup_events
    ADD COLUMN scan_mode text NOT NULL DEFAULT 'single',
    ADD COLUMN source_type text NOT NULL DEFAULT 'profile',
    ADD COLUMN source_url text NOT NULL DEFAULT '',
    ADD COLUMN product_crawl_job_id uuid;

ALTER TABLE lookup_events
    ADD CONSTRAINT lookup_events_scan_mode_check
        CHECK (scan_mode IN ('single', 'manual_loaded', 'automatic')),
    ADD CONSTRAINT lookup_events_source_type_check
        CHECK (source_type IN ('profile', 'member', 'post_author', 'comment_author'));
