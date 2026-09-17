ALTER TABLE license_keys ADD COLUMN IF NOT EXISTS features jsonb NOT NULL DEFAULT '{}';
ALTER TABLE account_subscriptions ADD COLUMN IF NOT EXISTS features jsonb NOT NULL DEFAULT '{}';
