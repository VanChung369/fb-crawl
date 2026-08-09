CREATE TABLE facebook_users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facebook_uid text,
    facebook_username text,
    normalized_username text,
    display_name text,
    profile_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT facebook_users_facebook_uid_key UNIQUE (facebook_uid),
    CONSTRAINT facebook_users_normalized_username_key
        UNIQUE (normalized_username),
    CONSTRAINT facebook_users_profile_url_key UNIQUE (profile_url),
    CONSTRAINT facebook_users_identity_required CHECK (
        NULLIF(btrim(facebook_uid), '') IS NOT NULL
        OR NULLIF(btrim(normalized_username), '') IS NOT NULL
        OR NULLIF(btrim(profile_url), '') IS NOT NULL
    )
);

CREATE TABLE phone_numbers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    normalized_phone text NOT NULL,
    display_phone text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT phone_numbers_normalized_phone_key UNIQUE (normalized_phone),
    CONSTRAINT phone_numbers_normalized_phone_format CHECK (
        normalized_phone ~ '^\+[0-9]{8,15}$'
    )
);

CREATE TABLE user_phone_evidence (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facebook_user_id bigint NOT NULL
        REFERENCES facebook_users (id) ON DELETE CASCADE,
    phone_number_id bigint NOT NULL
        REFERENCES phone_numbers (id) ON DELETE RESTRICT,
    origin text NOT NULL DEFAULT '',
    source text NOT NULL DEFAULT '',
    source_url text NOT NULL DEFAULT '',
    provider text NOT NULL DEFAULT '',
    correlation_id text NOT NULL DEFAULT '',
    confidence text NOT NULL DEFAULT 'unknown',
    first_captured_at timestamptz NOT NULL,
    last_captured_at timestamptz NOT NULL,
    evidence_count integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_phone_evidence_origin_check CHECK (
        origin IN ('fbnumber', 'fb_crawl')
    ),
    CONSTRAINT user_phone_evidence_count_check CHECK (evidence_count > 0),
    CONSTRAINT user_phone_evidence_capture_order_check CHECK (
        last_captured_at >= first_captured_at
    ),
    CONSTRAINT user_phone_evidence_identity_key UNIQUE (
        facebook_user_id,
        phone_number_id,
        origin,
        source,
        source_url,
        provider
    )
);

CREATE INDEX user_phone_evidence_phone_number_id_idx
    ON user_phone_evidence (phone_number_id);

CREATE INDEX user_phone_evidence_user_origin_idx
    ON user_phone_evidence (
        facebook_user_id,
        origin,
        confidence,
        last_captured_at DESC
    );

CREATE TABLE enrichment_attempts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facebook_user_id bigint NOT NULL
        REFERENCES facebook_users (id) ON DELETE CASCADE,
    provider text NOT NULL DEFAULT '',
    status text NOT NULL,
    checked_at timestamptz NOT NULL,
    correlation_id text NOT NULL DEFAULT '',
    error_code text NOT NULL DEFAULT '',
    values_found integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT enrichment_attempts_status_check CHECK (
        status IN ('found', 'not_found', 'rate_limited', 'failed')
    ),
    CONSTRAINT enrichment_attempts_values_found_check CHECK (
        values_found >= 0
    )
);

CREATE INDEX enrichment_attempts_user_provider_checked_idx
    ON enrichment_attempts (facebook_user_id, provider, checked_at DESC);

CREATE VIEW facebook_user_phone_slots AS
WITH ranked_evidence AS (
    SELECT
        evidence.id,
        evidence.facebook_user_id,
        evidence.phone_number_id,
        evidence.origin,
        row_number() OVER (
            PARTITION BY evidence.facebook_user_id, evidence.origin
            ORDER BY
                CASE
                    WHEN evidence.origin = 'fb_crawl' THEN
                        CASE evidence.confidence
                            WHEN 'profile_field' THEN 1
                            WHEN 'strong_pattern' THEN 2
                            WHEN 'weak_pattern' THEN 3
                            ELSE 4
                        END
                    ELSE 0
                END,
                evidence.last_captured_at DESC,
                evidence.id DESC
        ) AS preference_rank
    FROM user_phone_evidence AS evidence
)
SELECT
    users.id,
    users.facebook_uid,
    users.facebook_username,
    users.normalized_username,
    users.display_name,
    users.profile_url,
    max(numbers.normalized_phone) FILTER (
        WHERE ranked.origin = 'fbnumber'
    ) AS phone_1,
    max(numbers.normalized_phone) FILTER (
        WHERE ranked.origin = 'fb_crawl'
    ) AS phone_2,
    users.created_at,
    users.updated_at
FROM facebook_users AS users
LEFT JOIN ranked_evidence AS ranked
    ON ranked.facebook_user_id = users.id
    AND ranked.preference_rank = 1
LEFT JOIN phone_numbers AS numbers
    ON numbers.id = ranked.phone_number_id
GROUP BY users.id;
