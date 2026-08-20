# Run 3 Vertical Expansion Design

## Goal

Expand BuzzBot's controlled official Georgia Tech document corpus across student-information
verticals in one resumable run, while adding embedded-text PDF support and page-aware citations.

## Approved implementation scope

- Extend the existing document registry with `vertical`, `adapter`, accepted content types,
  freshness, and explicit path prefixes.
- Keep discovery source-controlled: declared HTTPS hosts, seeds, and paths only. `max_urls` is a
  fail-closed ceiling after canonicalization and deduplication.
- Add one aggregate Run 3 command. It freezes a heterogeneous `source + URL` manifest in the
  existing ingestion run tables and resumes from that snapshot without rediscovery.
- Route fetched resources by response `Content-Type` through the existing HTML extractor or an
  embedded-text PDF extractor. PDFs are chunked per page and retain page metadata.
- Keep transactional in-place document replacement. A failed URL must preserve its last trusted
  document, chunks, and embeddings.
- Propagate PDF page metadata to retrieval and API citations.
- Keep PostgreSQL, pgvector, FTS, RRF, and the existing CrossEncoder. Align the FTS index with the
  query expression and support multiple source names per source type/vertical.
- Verify with automated tests and a bounded HTML/PDF run only. Do not run full Run 3 ingestion.

## Explicit deferrals

- OCR, scanned/encrypted PDF ingestion, and browser automation.
- `institutional_facts`, SQL/RAG routing, document-version history, and a second search database.
- Full-corpus answer-quality tuning and authenticated student data.

## Data and failure semantics

The aggregate run stores short unit keys in `ingestion_run_units` and the immutable mapping to
`source`, `url`, `adapter`, and `vertical` in `ingestion_runs.scope_json`. Planning fails before any
unit executes if discovery fails or a source exceeds its ceiling. Execution failures produce
`PARTIAL`; auth is a hard stop and repeated rate limiting pauses the run through existing shared
orchestration.

HTML and PDF updates use the current per-URL transaction. Successful changes atomically replace
chunks; failures roll back. No implicit deletion occurs.

## Acceptance

- Existing 313-test baseline remains green.
- Registry/adapters reject external, out-of-path, and undeclared content types.
- Aggregate resume performs no discovery and successful units are not refetched.
- PDF extraction rejects oversized, encrypted, broken, or textless resources and publishes no
  partial data.
- PDF chunks and returned citations identify the exact source page.
- The FTS query and GIN expression match.
- Bounded verification exercises at most two HTML URLs and one PDF URL; no full ingestion runs.
