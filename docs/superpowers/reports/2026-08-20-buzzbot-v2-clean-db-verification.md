# BuzzBot v2 clean-database verification

Date: 2026-08-20

## Scope

- Switch every runtime and operational database consumer through one `DATABASE_URL`.
- Apply all Alembic migrations to an empty `buzzbot_v2` PostgreSQL database.
- Sync one controlled document source and one Fall 2026 OSCAR subject only.
- Verify failed publication isolation, readiness, SQL retrieval, checkpointing, and the v2 API.
- Do not collect all document sources and do not add term-wide subject orchestration.

## Results

- Empty database migrated through Alembic revision `003`; pgvector and all application tables were
  created from scratch.
- `gt-registrar` probe returned `READY`; sync indexed one document, two chunks, and two embeddings.
- Initial Fall 2026 CS fetch parsed all 1,779 sections but correctly failed validation on three
  arranged sections whose OSCAR records have no meeting table.
- The failed version retained six diagnostic errors and zero course, section, or meeting rows. It
  did not publish or supersede anything.
- The parser now reads schedule type from the section detail body. A source section with an explicit
  schedule type and no meeting rows is preserved without inventing dates or meetings.
- Offline replay of the saved OSCAR snapshot validated 1,779/1,779 sections with zero failures.
- The controlled retry published 178 courses, 1,779 sections, and 1,779 meetings. The three arranged
  sections remain queryable with zero fabricated meeting rows.
- `/live`, `/ready`, `/stats`, `/usage`, and `/v2/chat` all returned HTTP 200 against `buzzbot_v2`.
  Readiness reported database, official documents, published schedule, current freshness, and the
  PostgreSQL LangGraph checkpointer as healthy.
- The CS 7650 API query returned three Fall 2026 offerings with three citations and no LLM call.
- Tracked OpenAI cost remained about `$0.0003` against the hard `$3.00` application ceiling.
- PostgreSQL integration suite: 9 passed. Full suite: 182 passed, 9 skipped.
- The old `buzzbot` database retained its 25,978 documents and still had zero schedule versions.
  No integration-test versions remained in `buzzbot_v2`.

## Configuration fix

Previously, async application sessions used `DATABASE_URL`, while sync ingestion, Alembic, and one
audit script could use a separate legacy default. Sync URLs are now derived from `DATABASE_URL` by
the shared settings object, and the obsolete `DATABASE_URL_SYNC` path has been removed.

## Current database contents

| Item | Count |
|---|---:|
| Controlled sources | 1 |
| Documents | 1 |
| Chunks | 2 |
| Embeddings | 2 |
| Failed schedule versions | 1 |
| Published schedule versions | 1 |
| Published courses | 178 |
| Published sections | 1,779 |
| Published meetings | 1,779 |

The failed version is intentionally retained as a small audit record. It contains no normalized
schedule rows and cannot be returned by published-only retrieval.
