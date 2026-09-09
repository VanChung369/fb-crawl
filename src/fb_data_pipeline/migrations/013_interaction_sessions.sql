CREATE TABLE interaction_sessions (
    id uuid PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    client_session_id uuid NOT NULL,
    source_url text NOT NULL CHECK (length(source_url) BETWEEN 1 AND 2048),
    kind text NOT NULL CHECK (kind IN ('comments', 'reactions')),
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'stopped')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (account_id, client_session_id)
);
CREATE INDEX interaction_sessions_account_time_idx
    ON interaction_sessions(account_id, created_at DESC, id DESC);

CREATE TABLE session_people (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES interaction_sessions(id) ON DELETE CASCADE,
    identity_key text NOT NULL,
    identity jsonb NOT NULL,
    lookup_event_id bigint REFERENCES lookup_events(id) ON DELETE SET NULL,
    lookup_state text NOT NULL DEFAULT 'not_looked_up'
        CHECK (lookup_state IN ('not_looked_up', 'processing', 'found', 'not_found', 'failed', 'quota_exceeded', 'unavailable')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (session_id, identity_key),
    UNIQUE (session_id, id)
);
CREATE INDEX session_people_event_idx ON session_people(lookup_event_id)
    WHERE lookup_event_id IS NOT NULL;

CREATE TABLE session_interactions (
    session_id uuid NOT NULL REFERENCES interaction_sessions(id) ON DELETE CASCADE,
    client_row_id uuid NOT NULL,
    row_revision integer NOT NULL CHECK (row_revision > 0),
    interaction_id text NOT NULL CHECK (length(interaction_id) BETWEEN 1 AND 512),
    synthetic boolean NOT NULL,
    parent_id text NOT NULL CHECK (length(parent_id) <= 512),
    kind text NOT NULL CHECK (kind IN ('comment', 'reply', 'reaction')),
    person_id uuid NOT NULL,
    identity jsonb NOT NULL,
    text text NOT NULL CHECK (length(text) <= 10000),
    observed_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL,
    PRIMARY KEY (session_id, client_row_id),
    FOREIGN KEY (session_id, person_id) REFERENCES session_people(session_id, id) ON DELETE CASCADE
);
CREATE INDEX session_interactions_time_idx
    ON session_interactions(session_id, ingested_at DESC, client_row_id DESC);
CREATE INDEX session_interactions_person_idx ON session_interactions(session_id, person_id);
