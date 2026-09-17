ALTER TABLE interaction_sessions DROP CONSTRAINT interaction_sessions_kind_check;
ALTER TABLE interaction_sessions ADD CONSTRAINT interaction_sessions_kind_check
    CHECK (kind IN ('comments', 'reactions', 'friends', 'members'));
ALTER TABLE session_interactions DROP CONSTRAINT session_interactions_kind_check;
ALTER TABLE session_interactions ADD CONSTRAINT session_interactions_kind_check
    CHECK (kind IN ('comment', 'reply', 'reaction', 'friend', 'member'));
