# Subscription contact allowances

Usage and contact deduplication are keyed by account, calendar month in
`LEAD_FINDER_TIMEZONE`, and quota scope. Scope `0` is the default plan; a
purchased scope is the `account_subscriptions.id`.

- Activating a new effective subscription starts a separate allowance. For
  example, 479 contacts on the previous key do not consume a new 1,000 limit.
- Expiry or revocation returns to the default plan's separate allowance. Paid
  contacts do not consume its 100 limit. Any free contacts already used during
  that month still count against the free allowance.
- Another key redeemed while a subscription is active remains scheduled after
  the existing subscription(s). It gets its allowance when it becomes active.
- Monthly reset remains on the calendar month boundary. This change does not
  switch billing to monthly anniversaries of the activation date.
- Reopening the same Facebook user in the same scope/month is not charged again.
  Opening that user in a different subscription/month uses the new allowance.

## Applying the fix

Stop API and worker processes that write usage, then run the existing
`fb-crawl pipeline migrate` command against the intended database. Start the
updated backend and reload the rebuilt extension (`lead-finder/.output/chrome-mv3`).
The new backend requires migration `024_subscription_quota_scopes`.

The migration uses each reveal timestamp to assign existing usage to the key
that was active when the contact was opened. It preserves known usage on the
currently active key instead of indiscriminately zeroing every account.

Admin contact deletion may have removed old reveal records while preserving
the aggregate counter. Those historical charges cannot reliably be assigned
to a particular key. The migration archives the difference in
`legacy_unattributed_contact_usage` for reconciliation; it does not guess a
scope or let those charges exhaust the default allowance. Such untraceable
charges are not deducted from a live allowance after migration.

The entitlements response adds `quota_period_start`. The extension combines it
with the subscription ID to discard usage from old periods, including late
lookup responses. Saved scan counters are refreshed from current account
entitlements because they do not identify their original quota scope.
