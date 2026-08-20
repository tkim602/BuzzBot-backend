# Run 3 Vertical Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a controlled aggregate Run 3 document run with embedded-text PDF ingestion and page-aware retrieval, reusing BuzzBot's existing transactional pipeline.

**Architecture:** Registry-driven sources use existing source-specific adapters or one bounded declared-path adapter. The aggregate runner snapshots source/URL manifest entries into existing run JSON and executes short unit keys through the existing orchestration kernel. HTML and PDF converge before shared chunk/index storage.

**Tech Stack:** Python 3.12, httpx, lxml, pdfplumber, SQLAlchemy/Alembic, PostgreSQL/pgvector, pytest, Ruff, mypy.

---

### Task 1: Registry and bounded adapter dispatch

**Files:** `ingestion/documents/registry.py`, `ingestion/documents/discovery.py`, `ingestion/documents/sync_source.py`, `ingestion/sources.yaml`, `tests/test_document_registry.py`, `tests/test_document_discovery.py`

- [ ] Add failing tests for required Run 3 metadata, declared path acceptance, file/content-type restrictions, and max-URL fail-closed behavior.
- [ ] Run the focused tests and confirm they fail for missing fields/dispatch.
- [ ] Add the smallest registry fields and reuse `bounded_urls`; replace source-name branching with adapter lookup while preserving current adapter behavior.
- [ ] Add controlled official Run 3 source declarations with explicit seeds, roots, paths, content types, freshness, and ceilings.
- [ ] Run focused tests and commit.

### Task 2: Aggregate immutable manifest and resume

**Files:** `ingestion/documents/sync_all.py`, `ingestion/documents/cli.py`, `Makefile`, `tests/test_document_run3_runner.py`

- [ ] Add failing tests that a fresh run freezes all source/URL entries, planning errors execute nothing, resume does not rediscover, and per-vertical summaries are derived from unit results.
- [ ] Run the tests and confirm the aggregate runner is absent.
- [ ] Implement `sync_run3` by composing existing discovery, `create_run`, `plan_run`, `run_batch`, and `sync_document_url`; store the immutable manifest in `scope_json` and use short deterministic unit keys.
- [ ] Expose `sync-all --profile run3`, `--verification-limit`, and resume options plus `make sync-gt-all`/`resume-gt-all`.
- [ ] Run focused tests and commit.

### Task 3: Embedded-text PDF extraction and page chunks

**Files:** `ingestion/documents/pdf.py`, `ingestion/documents/sync.py`, `tests/test_document_pdf.py`, `tests/test_document_sync.py`

- [ ] Add fixture-generated PDF tests for page retention, content-type routing, byte/page ceilings, textless/encrypted rejection, and transaction preservation.
- [ ] Run focused tests and confirm PDF responses currently enter the HTML path or fail.
- [ ] Implement byte-based `pdfplumber` extraction using embedded text only. Return page-numbered text, choose a nonblank metadata/first-line title, and fail closed for unusable input.
- [ ] Extend the existing fetched-document/store path so PDF pages are chunked independently with `content_type`, `page_start`, and `page_end` metadata; increment chunking version.
- [ ] Run focused and document integration tests and commit.

### Task 4: Page-aware citations and multi-source retrieval

**Files:** `app/retrieval/documents.py`, `app/graph/state.py`, `app/graph/workflow.py`, `app/schemas/chat.py`, `app/api/agent.py`, `app/api/chat.py`, `app/rag/answerer.py`, `tests/test_document_retrieval.py`, `tests/test_graph_workflow.py`, `tests/test_agent_api.py`

- [ ] Add failing tests that one source type maps to multiple sources and a PDF evidence quote returns its exact page.
- [ ] Run focused tests and confirm the one-to-one map and missing citation field fail.
- [ ] Change source-type mapping values to tuples, propagate chunk metadata into document evidence, and copy the selected evidence page into exact-grounded citations.
- [ ] Add nullable `page` to internal/API citation types without changing HTML citations.
- [ ] Run focused tests and commit.

### Task 5: Align the PostgreSQL FTS index

**Files:** `app/rag/retrieval.py`, `db/migrations/versions/005_document_fts_metadata.py`, `tests/test_retrieval.py`, `tests/integration/test_document_retrieval.py`

- [ ] Add a failing SQL-expression regression proving query and index both cover title, headings, and chunk text.
- [ ] Replace `concat_ws` with one immutable `coalesce(...) || ...` expression shared by query and migration.
- [ ] Add migration 005 to replace the old chunk-only GIN expression index.
- [ ] Run migration from 004 to head and PostgreSQL retrieval integration tests; commit.

### Task 6: Documentation and bounded verification

**Files:** `docs/ingestion.md`, `docs/sources.md`

- [ ] Document exact fresh/resume/verification commands, status meanings, PDF limitations, and the fact that full Run 3 ingestion is operator-run.
- [ ] Run the full unit suite, DB integration suite, Ruff, mypy, migration head/check, diff check, and secret scan.
- [ ] Probe the declared seeds and perform only the approved bounded HTML/PDF verification with a no-cost fake embedding function or operator-provided environment; do not run paid/full ingestion.
- [ ] Commit verified changes and report exact commands and expected summary.
