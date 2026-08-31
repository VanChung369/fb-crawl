ALTER TABLE lookup_events
    ADD COLUMN revealed_phone_number_id bigint
        REFERENCES phone_numbers (id),
    ADD COLUMN revealed_observed_at timestamptz,
    ADD CONSTRAINT lookup_events_revealed_phone_snapshot_check
        CHECK (
            (revealed_phone_number_id IS NULL)
            = (revealed_observed_at IS NULL)
        );

CREATE INDEX lookup_events_revealed_phone_idx
    ON lookup_events (revealed_phone_number_id)
    WHERE revealed_phone_number_id IS NOT NULL;
