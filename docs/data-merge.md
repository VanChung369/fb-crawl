# Data merge and quality report

`data merge` combines unified public and authenticated user CSV output before
external enrichment or database storage.

```powershell
fb-crawl data merge runtime/output/*.csv `
  --output runtime/output/users-master.csv `
  --report runtime/output/quality-report.json
```

The command accepts explicit CSV paths and glob patterns. CSV files without the
unified `profile_url` identity schema, such as message and inspect output, are
skipped and listed in `skipped_files`. The selected output file is excluded
from its own glob automatically.

## Identity and merge rules

Rows are linked only by stable identity aliases:

- numeric `user_id`;
- normalized Facebook profile URL;
- case-insensitive `username`.

Names are never used as merge keys. A row containing both a numeric UID and a
vanity profile URL safely bridges those aliases. Multi-value fields such as
phone numbers, phone sources, crawl sources, field evidence, languages, and
reaction types are unioned in first-seen order. Phone values are deduplicated
by digits while their original display form is retained.

Repaired or verified identity values win over legacy/suspicious values. Scalar
disagreements are not silently discarded: the chosen value, alternatives, and
input file/line locations are written to `conflict_details` in the report.

Both the master CSV and JSON report are written atomically. UID and phone
columns remain text so leading zeroes are preserved.

## Quality report

The JSON report includes:

- input, skipped, valid, issue, and unidentified row counts;
- records written and duplicates merged;
- conflict count and detailed evidence;
- repair-candidate count;
- coverage and missing counts for UID, name, username, phone, address, current
  city, and birth year.

Use the report to select another `authenticated repair` or profile enrichment
pass before sending records to a future external provider or PostgreSQL. The
selection can be generated automatically with
[`data plan`](data-plan.md).
