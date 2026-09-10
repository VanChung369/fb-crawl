CREATE TABLE account_leads (
    id uuid PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    facebook_uid text,
    username text,
    status text NOT NULL DEFAULT 'unprocessed'
        CHECK (status IN ('unprocessed', 'potential', 'called', 'closed', 'no_answer')),
    notes text NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (facebook_uid IS NOT NULL OR username IS NOT NULL),
    UNIQUE (account_id, facebook_uid),
    UNIQUE (account_id, username)
);
