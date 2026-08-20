# Document Expansion Run 1 Verification

**Date:** 2026-08-20
**Branch:** `data/document-expansion-run1`
**Database:** `buzzbot_v2`

## Implemented

- Reused `ingestion_runs` and `ingestion_run_units` with canonical URL unit keys.
- Added separate Registrar and Catalog discovery adapters.
- Made `max_urls` a fail-closed safety ceiling. Discovery never silently truncates.
- Added the separate, explicit `verification_limit` smoke option.
- Added immutable manifest resume and per-URL result JSON through the existing orchestration.
- Kept document/chunk/embedding replacement atomic per URL with no implicit deletion.
- Kept healthy unchanged documents free from re-embedding and added repair for invalid stored chunks.

## Discovery coverage

One official index request per source produced:

| Source | Production manifest | Safety ceiling | Longest URL |
|---|---:|---:|---:|
| `gt-registrar` | 24 URLs | 50 | 81 characters |
| `gt-catalog` | 91 URLs | 150 | 41 characters |

Both manifests fit the existing 128-character ingestion unit key. The count is a current index
snapshot, not a promise that Georgia Tech will never add URLs; exceeding the configured ceiling
fails planning with `MAX_URLS_EXCEEDED`.

## Bounded live verification

Only two URLs per source were synchronized. Full source ingestion was not run.

- Registrar run `47077cb0-2579-43fd-b2dd-4ec398be5b85`: 2 planned, 2 succeeded.
- Catalog run `14b27743-e9de-4640-a8fa-5c160bd9a0e0`: 2 planned, 2 succeeded.
- Catalog index: 1,991 characters, 2 chunks, 662 tokens, minimum chunk 162 tokens.
- ACCT page: 504 characters, 1 chunk, 95 tokens.
- Citation smoke: `ACCT 2101` returned
  `https://catalog.gatech.edu/coursesaz/acct` from `gt-catalog` using `exact_code` retrieval.
- Usage after verification: `$0.0013 / $3.00`.

The bounded Catalog run initially exposed two defects that are now regression tested: official
trailing-slash canonical redirects were rejected, and catalog list rows were misclassified as
headings. The final bounded run and stored chunk checks above were executed after both fixes.

## Operator commands

Production runs include every discovered URL within the safety ceiling:

```bash
make migrate
make sync-doc-many source=gt-registrar
make sync-doc-many source=gt-catalog
```

Expected successful summary shape:

```json
{"provider":"official-documents","status":"COMPLETED","planned":24,"succeeded":24,"failed":0,"remaining":0,"complete":true}
```

Registrar currently plans 24 URLs and Catalog currently plans 91, so the exact `planned` value is
source-specific and may change with the official index. A nonzero exit with
`stop_reason=MAX_URLS_EXCEEDED` requires reviewing the newly discovered URLs before increasing the
source ceiling.

Resume a paused or interrupted immutable manifest without rediscovery:

```bash
make resume-doc-run source=gt-registrar run_id=<RUN_ID>
make resume-doc-run source=gt-catalog run_id=<RUN_ID>
```

Bounded smoke only:

```bash
make sync-doc-many source=gt-registrar verification_limit=2
make sync-doc-many source=gt-catalog verification_limit=2
```

