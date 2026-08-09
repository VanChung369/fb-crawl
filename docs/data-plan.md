# Missing-data enrichment plan

`data plan` turns a unified master CSV into bounded, typed profile targets for
`authenticated batch`. It closes the local feedback loop without requiring a
database or external provider.

```powershell
fb-crawl data plan runtime/output/users-master.csv `
  --missing phone,address,current_city,birth_year `
  --cooldown-days 30 `
  --limit 100 `
  --output runtime/targets/enrichment.txt `
  --report runtime/output/enrichment-plan.json
```

The output contains one normalized `profile:URL` line per selected identity and
can be passed directly to the authenticated batch command:

```powershell
fb-crawl authenticated batch `
  --input runtime/targets/enrichment.txt `
  --enrich-profiles `
  --profile-fields phone,address,current_city,birth_date `
  --headless `
  --output runtime/output/enrichment.csv
```

Run `data merge` again with the enrichment output to update the master CSV.

## Selection policy

Supported `--missing` values are `phone`, `website`, `address`, `current_city`,
`hometown`, `birth_date`, `birth_year`, `bio`, `workplace`, `education`,
`gender`, `languages`, and `relationship_status`. `birth_year` plans the
authenticated `birth_date` field because the year is derived from that visible
profile value.

Candidates must have a valid Facebook profile URL and at least one requested
missing field. By default a record is also eligible when its numeric UID is
missing or its name is blank/suspicious. Use `--skip-repair` to select only
missing enrichment fields.

Priority is deterministic:

1. identity-repair candidates;
2. records missing more requested fields;
3. records never enriched or enriched least recently;
4. original master-file order.

`--cooldown-days` defaults to 30. An incomplete record with a recent
`last_enriched_at` is skipped so a known-not-visible field is not requested on
every run. `--force` bypasses this cooldown but still excludes complete rows.

The JSON report records selected/eligible/limited counts, skipped reasons,
duplicate targets, field coverage needed by the selected batch, normalized
`profile_fields`, and per-target reasons. Target and report files are written
atomically. Identifiers remain strings throughout CSV processing.
