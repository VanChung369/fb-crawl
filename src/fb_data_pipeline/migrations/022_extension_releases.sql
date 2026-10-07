CREATE TABLE IF NOT EXISTS extension_releases (
    id uuid PRIMARY KEY,
    version text NOT NULL UNIQUE,
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS extension_update_policy (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_release_id uuid REFERENCES extension_releases(id) ON DELETE RESTRICT,
    announcement_enabled boolean NOT NULL DEFAULT false,
    enforcement_enabled boolean NOT NULL DEFAULT false,
    min_supported_version text NOT NULL DEFAULT '0.0.0',
    message text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT (announcement_enabled OR enforcement_enabled) OR active_release_id IS NOT NULL)
);
INSERT INTO extension_update_policy(singleton) VALUES (true) ON CONFLICT DO NOTHING;
