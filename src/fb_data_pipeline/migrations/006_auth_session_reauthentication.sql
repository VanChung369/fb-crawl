ALTER TABLE auth_sessions
    ADD COLUMN authenticated_at timestamptz;

WITH RECURSIVE session_roots AS (
    SELECT id, rotated_from_id, id AS root_id
    FROM auth_sessions
    WHERE rotated_from_id IS NULL
    UNION ALL
    SELECT child.id, child.rotated_from_id, parent.root_id
    FROM auth_sessions AS child
    JOIN session_roots AS parent ON child.rotated_from_id = parent.id
)
UPDATE auth_sessions AS session
SET authenticated_at = root_session.created_at
FROM session_roots
JOIN auth_sessions AS root_session ON root_session.id = session_roots.root_id
WHERE session.id = session_roots.id
  AND session.authenticated_at IS NULL;

ALTER TABLE auth_sessions
    ALTER COLUMN authenticated_at SET NOT NULL,
    ADD CONSTRAINT auth_sessions_authenticated_at_check
        CHECK (authenticated_at <= last_used_at);
