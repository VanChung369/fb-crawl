-- Keep the default allowance and each purchased subscription independent.
-- Stop API/worker writers while applying this migration and restart on the new code.
LOCK TABLE usage_monthly, account_contact_reveals IN ACCESS EXCLUSIVE MODE;

ALTER TABLE usage_monthly
    ADD COLUMN quota_scope bigint NOT NULL DEFAULT 0 CHECK (quota_scope >= 0);
ALTER TABLE account_contact_reveals
    ADD COLUMN quota_scope bigint NOT NULL DEFAULT 0 CHECK (quota_scope >= 0);

-- PostgreSQL truncates generated names for long unique constraints. Discover
-- the old names rather than depending on that truncation.
DO $$
DECLARE legacy record;
BEGIN
    FOR legacy IN
        SELECT conrelid::regclass AS table_name, conname
        FROM pg_constraint
        WHERE conrelid IN ('usage_monthly'::regclass, 'account_contact_reveals'::regclass)
          AND contype = 'u'
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', legacy.table_name, legacy.conname);
    END LOOP;
END $$;

ALTER TABLE usage_monthly
    ADD CONSTRAINT usage_monthly_scope_key
    UNIQUE (account_id, period_start, quota_scope);
ALTER TABLE account_contact_reveals
    ADD CONSTRAINT account_contact_reveals_scope_key
    UNIQUE (account_id, facebook_user_id, period_start, quota_scope);

-- Attribute existing charged contacts to the subscription active when opened.
-- Include subsequently revoked subscriptions, but only before their revocation.
UPDATE account_contact_reveals AS reveals
SET quota_scope = COALESCE((
    SELECT subscription.id
    FROM account_subscriptions AS subscription
    WHERE subscription.account_id = reveals.account_id
      AND subscription.starts_at <= reveals.revealed_at
      AND reveals.revealed_at < subscription.ends_at
      AND (subscription.revoked_at IS NULL OR reveals.revealed_at < subscription.revoked_at)
    ORDER BY subscription.starts_at DESC, subscription.id DESC
    LIMIT 1
), 0);

-- Admin deletion can remove a reveal without reducing the old monthly count.
-- Preserve those untraceable charges for reconciliation, rather than charging
-- them to the default allowance or guessing which purchased key owned them.
CREATE TABLE legacy_unattributed_contact_usage (
    account_id bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    period_start date NOT NULL,
    used_contact_count integer NOT NULL CHECK (used_contact_count > 0),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, period_start)
);

INSERT INTO legacy_unattributed_contact_usage (
    account_id, period_start, used_contact_count
)
SELECT usage.account_id, usage.period_start,
       usage.used_contact_count - count(reveals.id)::integer
FROM usage_monthly AS usage
LEFT JOIN account_contact_reveals AS reveals
  ON reveals.account_id = usage.account_id AND reveals.period_start = usage.period_start
GROUP BY usage.account_id, usage.period_start, usage.used_contact_count
HAVING usage.used_contact_count > count(reveals.id);

UPDATE usage_monthly AS usage
SET used_contact_count = (
    SELECT count(*)::integer
    FROM account_contact_reveals AS reveals
    WHERE reveals.account_id = usage.account_id
      AND reveals.period_start = usage.period_start
      AND reveals.quota_scope = 0
);

INSERT INTO usage_monthly (
    account_id, period_start, quota_scope, used_contact_count, created_at, updated_at
)
SELECT account_id, period_start, quota_scope, count(*)::integer,
       min(revealed_at), max(revealed_at)
FROM account_contact_reveals
WHERE quota_scope <> 0
GROUP BY account_id, period_start, quota_scope;
