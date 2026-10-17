# FBNumber masked contact fix — 2026-10-17

FBNumber responses containing `number: 0966777*` and `number2: 0963005272`
previously produced two phone records, including the invented `+84966777`.
Normalization removed the mask characters before checking the digit count.

## Resulting behavior

- Phone normalization rejects redaction markers and letters before removing
  normal formatting. Vietnamese mobile and fixed-line contact lengths are
  checked separately so a truncated cached fixed line cannot pass as a mobile.
- Both the phone-search adapter and the scans importer skip masked numbers and
  retain the complete second phone as `+84963005272`.
- Address selection tries location, address, city, and current_city for an
  unmasked value. An address such as `Thà****************` is not guessed or
  allowed to displace a complete alternative. If no complete field exists,
  address is absent; an existing complete stored profile is preserved by the
  existing profile upsert behavior.
- Truncated legacy cached phones are not treated as fresh found contacts;
  a new lookup can fetch the complete number. Polling an old invalid result
  returns `contact_result_unavailable` without revealing it or reserving quota.
- User listings and exports that use UserQueryRepository hide legacy truncated
  phones and masked addresses. Stored rows are not deleted by this change.

## Validation

The initial regression run failed on 17 cases with the expected masked-first
phone, address fallback, and old-cache errors. Additional listing/polling and
fixed-line regressions were also observed failing before their fixes.

**128 focused tests passed** across phone normalization, the real FBNumber
adapter with mock HTTP responses, scan imports, stored user reads, and contact
lookup/polling. An independent review identified a truncated fixed-line gap;
the added regressions now pass and the reviewer confirmed the fix.

Final full backend run (`python -m pytest -q --tb=short`): **1,515 passed,
93 skipped, 1 warning**. Database integration tests were skipped because
`TEST_DATABASE_URL` was not configured. The warning is the existing FastAPI /
Starlette test-client deprecation.

```powershell
python -m pytest tests/unit/data_pipeline/test_phone.py tests/unit/data_pipeline/test_fbnumber.py tests/unit/data_pipeline/test_fbnumber_scans.py tests/unit/data_pipeline/test_user_query_repository.py tests/unit/contacts/test_service.py -q --tb=short
```

No request to the live FBNumber endpoint or production database mutation was
made. Restart the backend on the updated code to apply the fix. This does not
reconstruct characters the provider withheld or rewrite historical database
records.

Numbering references consulted: [ITU Viet Nam numbering updates](https://www.ituob.org/issues/1195-en/)
and [VNPT Hanoi area-code conversion](https://hanoi.vnpt.vn/portal/web/tintuc_chitiet.asp?news_id=7380).
