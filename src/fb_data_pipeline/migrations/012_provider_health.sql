CREATE TABLE provider_health (
    provider_name text PRIMARY KEY,
    configured boolean NOT NULL,
    last_success_at timestamptz,
    safe_error_code text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL,
    CONSTRAINT provider_health_name_check CHECK (btrim(provider_name) <> ''),
    CONSTRAINT provider_health_error_check CHECK (length(safe_error_code) <= 128)
);

INSERT INTO provider_health (
    provider_name, configured, last_success_at, safe_error_code, updated_at
) VALUES ('fbnumber', false, NULL, '', CURRENT_TIMESTAMP)
ON CONFLICT (provider_name) DO NOTHING;
