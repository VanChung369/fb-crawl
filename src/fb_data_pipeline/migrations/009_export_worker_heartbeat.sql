CREATE TABLE product_worker_heartbeats (
    worker_kind text PRIMARY KEY,
    worker_id text NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    CONSTRAINT product_worker_heartbeats_kind_check
        CHECK (worker_kind IN ('export')),
    CONSTRAINT product_worker_heartbeats_worker_id_check
        CHECK (btrim(worker_id) <> '')
);
