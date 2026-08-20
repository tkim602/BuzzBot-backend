# Document Expansion Run 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded, resumable multi-URL ingestion for the controlled Registrar and Catalog sources.

**Architecture:** Two source-specific discovery functions produce normalized URL tuples. A thin document-source runner persists those URLs through the existing ingestion-run infrastructure and routes each URL through the existing document fetch/extract/chunk/embed transaction.

**Tech Stack:** Python 3.11+, asyncio, httpx, lxml, SQLAlchemy 2, PostgreSQL, pgvector, pytest

---

## Repository compatibility

- `IngestionRunUnit.unit_key` already stores strings up to 128 characters, which covers the
  discovered canonical URLs in this scope; no migration is required.
- `plan_run()` already freezes ordering and prevents replanning, and `run_batch()` already resumes
  pending URL units and records `result_json` per unit.
- `sync_document_source()` is intentionally one-seed-only. Its non-calendar fetch/store path will
  be extracted once and reused by the URL runner without changing academic-calendar behavior.
- `_store_document()` and `index_chunks()` already keep document/chunk/embedding replacement in one
  session transaction and skip embedding when the content hash is unchanged.

### Task 1: Add bounded Registrar and Catalog discovery

**Files:**
- Create: `ingestion/documents/registrar.py`
- Create: `ingestion/documents/catalog.py`
- Create: `tests/test_document_discovery.py`

- [ ] Write failing tests with small HTML fixtures proving relative-link resolution,
  allowlisted-prefix filtering, canonicalization, deduplication, source order, and that exceeding
  the `max_urls` safety ceiling fails rather than truncates.
- [ ] Run `PYTHONPATH=$PWD python3 -m pytest -q tests/test_document_discovery.py` and verify RED
  because the adapter modules do not exist.
- [ ] Implement one `discover_urls(source, html)` function in each adapter using
  `lxml.html`, `urllib.parse.urljoin`, `DocumentSource.allows()`, and `normalize_url()`.
  Registrar accepts only `/registration` and descendants. Catalog accepts only `/coursesaz/`
  and one-segment subject descendants.
- [ ] Run the focused test and verify GREEN.

### Task 2: Extract one safe URL sync path

**Files:**
- Modify: `ingestion/documents/sync.py`
- Modify: `tests/test_document_sync.py`
- Modify: `tests/integration/test_document_sync.py`

- [ ] Write failing tests for `sync_document_url(source, url, ...)`: one request, allowlist
  enforcement, unchanged-content no re-embedding, extraction failure preserving existing data,
  and embedding failure rolling back the document, chunks, and embeddings together.
- [ ] Run the focused unit test and verify RED because `sync_document_url()` is absent.
- [ ] Extract the existing non-calendar seed fetch into `sync_document_url()` while keeping
  `sync_document_source()` behavior and its academic-calendar adapter unchanged. Use a session
  context plus explicit commit only after `_store_document()` succeeds.
- [ ] Run unit and PostgreSQL integration tests and verify GREEN.

### Task 3: Persist and resume URL manifests

**Files:**
- Create: `ingestion/documents/sync_source.py`
- Create: `tests/test_document_source_runner.py`
- Modify: `ingestion/documents/cli.py`
- Modify: `Makefile`

- [ ] Write failing tests proving a fresh source run fetches its discovery page once, plans every
  discovered URL within `max_urls`, fails planning with `MAX_URLS_EXCEEDED` above the ceiling,
  records per-URL counts, maps auth/rate-limit/fetch/extract outcomes to the existing
  `UnitOutcome`, and a resume uses the stored manifest without rediscovery.
- [ ] Run `PYTHONPATH=$PWD python3 -m pytest -q tests/test_document_source_runner.py` and verify RED.
- [ ] Implement `sync_document_source_urls()` as a thin adapter around `create_run()`,
  `plan_run()`, `load_run_summary()`, and `run_batch()`. Dispatch directly by the two supported
  source names; do not add a base class or factory.
- [ ] Add CLI `sync-many --source ... [--verification-limit 2]` for explicit smoke runs and
  `sync-many --run-id ... --resume` for stored manifests. Add matching Make targets.
- [ ] Run runner and CLI tests and verify GREEN.

### Task 4: Verification and operator handoff

**Files:**
- Modify: `docs/ingestion.md`
- Create: `docs/superpowers/reports/2026-08-20-document-expansion-run1.md`

- [ ] Run migration from the current database with `alembic upgrade head`; no new revision is
  expected.
- [ ] Run focused tests, the full suite, PostgreSQL integration tests, Ruff, mypy, diff check, and
  secret scan.
- [ ] Run bounded live Registrar and Catalog discovery/sync with `--verification-limit 2` only. Never run the
  full source manifests.
- [ ] Run one retrieval citation smoke query against the bounded documents.
- [ ] Record exact operator commands, expected JSON summaries, coverage limits, and verification
  evidence, then stop.
