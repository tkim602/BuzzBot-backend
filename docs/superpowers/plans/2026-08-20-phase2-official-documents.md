# Phase 2 Official GT Documents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest a small controlled registry of authoritative Georgia Tech documents and expose citation-preserving hybrid retrieval without restoring broad crawling.

**Architecture:** A typed registry defines exact official roots, seeds, authority, and hard URL caps. Every source is probed with one public request before deterministic fetch/extract/chunk/index. Existing PostgreSQL FTS, pgvector, chunking, and RRF are reused; unchanged content hashes skip re-embedding.

**Tech Stack:** Python 3.11, httpx, lxml/trafilatura, SQLAlchemy/PostgreSQL, pgvector, OpenAI `text-embedding-3-small`, pytest.

## Constraints

- No generic `*.gatech.edu` crawl or Common Crawl fallback.
- Probe one configured seed per source; auth redirect, 429, incompatible body, or disallowed host stops that source.
- Initial live sync is capped at one document per source.
- Probe-only commands do not load `.env`, initialize OpenAI, or open the database.
- Only changed documents are chunked and embedded.
- Exact dates use only `gt-academic-calendar`; registration policy uses `gt-registrar`.
- Preserve canonical URL, title, source type, authority, fetched time, and edition metadata.

---

### Task 1: Controlled Registry and Bounded Document Probe

**Files:**
- Create: `ingestion/documents/__init__.py`
- Create: `ingestion/documents/registry.py`
- Create: `ingestion/documents/probe.py`
- Modify: `ingestion/sources.yaml`
- Test: `tests/test_document_registry.py`
- Test: `tests/test_document_probe.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class DocumentSource:
    name: str
    source_type: str
    authority: str
    allowed_roots: tuple[str, ...]
    seed_urls: tuple[str, ...]
    max_urls: int

async def probe_document_source(
    source: DocumentSource,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DocumentProbeResult: ...
```

- [ ] Write failing tests proving the registry contains Registrar, Catalog, Academic Calendar, OMSCS, and Admissions with explicit roots/seeds and caps no greater than 25.
- [ ] Write failing tests proving one request, auth/429 stop, HTTPS root enforcement, redirect-host rejection, and minimum extracted body checks.
- [ ] Implement literal registry loading from YAML and one-request probing using existing safe response concepts; do not reuse sitemap recursion.
- [ ] Run focused tests, Ruff, mypy, and commit `feat: add controlled GT document registry`.

---

### Task 2: Deterministic Changed-Only Document Sync

**Files:**
- Create: `ingestion/documents/sync.py`
- Create: `ingestion/documents/cli.py`
- Modify: `ingestion/index.py`
- Test: `tests/test_document_sync.py`
- Test integration: `tests/integration/test_document_sync.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class DocumentSyncResult:
    source: str
    outcome: str
    requests_used: int
    fetched: int
    changed: int
    chunks_indexed: int

async def sync_document_source(
    source: DocumentSource,
    session_factory,
    embed_fn,
    transport=None,
    max_documents=1,
) -> DocumentSyncResult: ...
```

- [ ] Write failing tests for probe failure preventing fetch/DB use, exactly one fetch after READY, conditional headers, canonical root enforcement, extraction failure, and compact counts.
- [ ] Write a PostgreSQL test publishing a fixture once, then syncing identical content and asserting the embedding function is not called and existing chunks remain.
- [ ] Implement explicit orchestration with existing `extract_content`, `normalize_url`, `content_hash`, `chunk_text`, and index functions. Store citation metadata on every chunk.
- [ ] Keep the CLI commands separate: `probe` never creates an embedding client; `sync` defaults to `text-embedding-3-small`.
- [ ] Run focused/full/DB tests, Ruff, mypy, and commit `feat: sync changed official GT documents`.

---

### Task 3: Typed Hybrid Policy Retrieval

**Files:**
- Create: `app/retrieval/documents.py`
- Modify: `app/retrieval/__init__.py`
- Test: `tests/test_document_retrieval.py`
- Test integration: `tests/integration/test_document_retrieval.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class PolicyQuery:
    text: str
    source_types: tuple[str, ...] = ()
    top_k: int = 5

async def search_policy_docs(
    session: AsyncSession,
    query: PolicyQuery,
    query_embedding: list[float],
) -> list[DocumentEvidence]: ...
```

- [ ] Write failing tests for typed validation, official-source filtering, calendar-only deadline authority, citation fields, and stable deduplication.
- [ ] Implement one wrapper over existing vector + FTS + RRF retrieval; do not duplicate ranking code or add another reranker.
- [ ] Run a PostgreSQL fixture test proving lexical/vector fusion and source filters preserve canonical citations.
- [ ] Run focused/full/DB tests, Ruff, mypy, and commit `feat: retrieve authoritative GT documents`.

---

### Task 4: One Bounded Live Document Smoke Run

- [ ] Probe each configured source once with one seed only; do not discover adjacent URLs.
- [ ] Sync at most one READY document per source and stop that source on auth, 429, or extraction incompatibility.
- [ ] Keep total embedding use under the existing `$3` application cap and record actual tracked cost.
- [ ] Run one registration-policy retrieval and one academic-calendar retrieval with citations.
- [ ] Record counts, unavailable sources, retrieval output metadata, and limitations without dumping document bodies.

## Verification

```bash
PYTHONPATH=$PWD python3 -m pytest -q
RUN_DB_TESTS=1 PYTHONPATH=$PWD python3 -m pytest -q tests/integration
python3 -m ruff check <changed files>
python3 -m mypy ingestion/documents app/retrieval/documents.py
git diff --check
```
