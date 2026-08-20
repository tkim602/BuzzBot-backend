# Academic Calendar XHR Ingestion Design

## Problem

The official Georgia Tech Academic Calendar page returns a valid static HTML shell, but its event rows are loaded later by JavaScript. Generic article extraction therefore indexed only the page heading and timezone note while reporting success.

The page's official script loads the selected academic year from `/calevents/proxy` and DataTables generates CSV, Excel, and PDF exports in the browser from that same JSON response. A plain automated request is rejected with HTTP 403, while the public browser request headers used by the page return the unauthenticated JSON successfully.

## Decision

Keep the existing controlled document registry and add one source-specific collector for `academic_calendar`. The collector will:

1. Reuse the one-request page probe and read the selected academic year from the static HTML.
2. Fetch `/calevents/proxy?year=<edition>&status=current` with the public XHR headers expected by the official site.
3. Validate the JSON before any database or embedding operation.
4. Convert every event into deterministic plain text and store it under the existing canonical calendar page URL.
5. Mark every event as an explicit Markdown section so chunking preserves event boundaries.
6. Reuse the existing content hash, embedding, and transactional document replacement path.

The current academic year only is in scope. Historical calendar enumeration, a headless browser, and client-generated CSV/PDF parsing are excluded until the public XHR becomes unusable.

## Validation

A collection is rejected when the payload is not a JSON object with a `data` list, contains fewer than 25 events, or any event is missing `id`, `date`, `semester`, `year`, `category`, or `event`. Semester codes are normalized to their official names, HTML in event descriptions is converted to text, and records are ordered deterministically by numeric weight and ID.

Validation finishes before `_store_document` opens a write transaction. Failed collection therefore cannot replace the currently indexed document. A changed valid collection replaces the existing document and chunks under the same canonical URL; an identical normalized collection skips re-embedding.

## Chunk Integrity

Every normalized event starts with `## Georgia Tech Academic Calendar <edition> — Event <id>` and contains its semester, category, date, and event text in the same section. Generic heading detection must treat `Field: value` lines as structured content rather than headings. Calendar sections use a 10-token minimum because a short official deadline is still a complete retrievable fact.

The normalized format change changes the document hash once, so the next successful sync replaces the incomplete index without a manual database edit. Automated tests require every input event ID to appear exactly once across the produced chunks. Other document source thresholds remain unchanged.

## Operational Behavior

`probe-doc source=gt-academic-calendar` remains a single, free request and verifies that the page advertises a current academic year. `sync-doc source=gt-academic-calendar` adds one JSON request, then embeds only when normalized content changed. No LLM is used for fetching or parsing.
