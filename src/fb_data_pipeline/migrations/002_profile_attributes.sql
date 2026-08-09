CREATE TABLE facebook_user_profiles (
    facebook_user_id bigint PRIMARY KEY
        REFERENCES facebook_users (id) ON DELETE CASCADE,
    address text,
    birth_date text,
    gender text,
    source_url text NOT NULL DEFAULT '',
    observed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT facebook_user_profiles_content_required CHECK (
        NULLIF(btrim(address), '') IS NOT NULL
        OR NULLIF(btrim(birth_date), '') IS NOT NULL
        OR NULLIF(btrim(gender), '') IS NOT NULL
    )
);

CREATE OR REPLACE VIEW facebook_user_phone_slots AS
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
    users.updated_at,
    max(profiles.address) AS address,
    max(profiles.birth_date) AS birth_date,
    max(profiles.gender) AS gender
FROM facebook_users AS users
LEFT JOIN ranked_evidence AS ranked
    ON ranked.facebook_user_id = users.id
    AND ranked.preference_rank = 1
LEFT JOIN phone_numbers AS numbers
    ON numbers.id = ranked.phone_number_id
LEFT JOIN facebook_user_profiles AS profiles
    ON profiles.facebook_user_id = users.id
GROUP BY users.id;
