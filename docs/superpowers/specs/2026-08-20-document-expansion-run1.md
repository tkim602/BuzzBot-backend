# Document Expansion Run 1 Design

**Status:** Approved

## Goal

Expand the controlled Registrar and Catalog document sources from one page to bounded,
resumable URL manifests without changing the existing document storage or embedding pipeline.

## Common execution

- One ingestion run represents one document source.
- Each immutable run unit is one normalized canonical URL.
- Fresh runs discover once, then allowlist, canonicalize, and deduplicate before checking the
  source `max_urls` safety ceiling. At or below the ceiling every URL enters the manifest; above
  it planning fails with `MAX_URLS_EXCEEDED` and no URL is silently dropped.
- Resume loads only the stored incomplete URLs and never performs discovery again.
- The existing ingestion orchestration kernel owns retries, status, and per-URL result summaries.
- A source run is complete only when every planned URL succeeds.

## Discovery adapters

- Registrar and Catalog have separate small adapters; there is no universal crawler or provider
  base class.
- Registrar discovers only official links under the configured registration path prefix.
- Catalog discovers only official course A-Z links under the configured course path prefix.
- Adapters resolve relative links, normalize URLs, remove fragments and duplicates, reject URLs
  outside their allowlist and preserve source order.
- `verification_limit` is a separate explicit smoke-test option applied only after the safety
  ceiling passes. Production runs do not set it.

## Storage safety

- Each canonical URL is fetched, extracted, chunked, embedded, and committed independently.
- Document metadata, content, chunks, and embeddings for one URL are replaced in one database
  transaction. Any failure rolls back that URL and preserves its previous trusted data.
- A failed or missing URL never causes implicit deletion.
- Content-hash equality updates fetch/citation metadata but skips re-embedding.

## Verification boundary

- Automated unit and PostgreSQL integration tests cover discovery, immutable manifests, resume,
  per-URL summaries, failed-update preservation, atomic replacement, and unchanged-content skips.
- Live verification may discover each source and sync at most two URLs.
- One retrieval smoke query must return a citation from the bounded documents.
- Full Registrar and Catalog ingestion is explicitly operator-run and excluded from this change.

## Deferred

- generic same-domain crawling, sitemaps, distributed workers, implicit deletion, tombstones,
  source-wide atomic publication, and new orchestration tables
