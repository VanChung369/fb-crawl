# Phone evidence master

Authenticated profile enrichment writes a sibling `*-phone-evidence.csv` when
visible profile fields, intro text, or post text contains a phone number. The
`data phone-merge` command combines those sidecars into one auditable master:

```powershell
fb-crawl data phone-merge runtime/output/*-phone-evidence.csv `
  --default-country-code 84 `
  --output runtime/output/phone-evidence-master.csv `
  --report runtime/output/phone-evidence-quality.json
```

`--default-country-code` defaults to `84`. With that setting, equivalent forms
such as `0912 345 678`, `84 912 345 678`, `+84 912 345 678`, and
`0084 912 345 678` share the normalized key `+84912345678`. Original display
text remains in `phone_number`; identifiers and phone values remain CSV text.

## Master schema

```text
user_id,profile_url,phone_number,normalized_phone,sources,source_urls,first_captured_at,last_captured_at,confidence,evidence_count,quality_status
```

One output row represents one Facebook identity plus one normalized phone
number. Repeated crawl rows increase `evidence_count`; sources and source URLs
are unioned in first-seen order. A vanity profile URL is preferred over an
equivalent numeric URL. Confidence uses the strongest observed value, with a
dedicated `profile_field` stronger than `strong_pattern` text evidence.
If `user_id` is blank but a numeric `profile.php?id=...` URL is valid, the
master recovers that UID while retaining `missing_uid` as an input-quality flag.

`quality_status=ok` means no structural issue was detected. Other statuses can
include missing UID, invalid profile/source URL, missing/invalid capture time,
unknown confidence, or an identity conflict. Invalid phone values and evidence
without any usable Facebook identity are excluded from the master but remain
listed by source file and line in the JSON report.

The report includes input/skipped files, row and deduplication counts, quality
issue totals and details, source/confidence counts, and coverage for numeric
UID, profile URL, timestamp, valid source URL, and dedicated profile-field
evidence. Both outputs are replaced atomically. Evidence CSV files are
explicitly skipped by the normal `data merge` user-record pipeline.
