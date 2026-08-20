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
5. Reuse the existing content hash, chunking, embedding, and transactional document replacement path.

The current academic year only is in scope. Historical calendar enumeration, a headless browser, and client-generated CSV/PDF parsing are excluded until the public XHR becomes unusable.

## Validation

A collection is rejected when the payload is not a JSON object with a `data` list, contains fewer than 25 events, or any event is missing `id`, `date`, `semester`, `year`, `category`, or `event`. Semester codes are normalized to their official names, HTML in event descriptions is converted to text, and records are ordered deterministically by numeric weight and ID.

Validation finishes before `_store_document` opens a write transaction. Failed collection therefore cannot replace the currently indexed document. A changed valid collection replaces the existing document and chunks under the same canonical URL; an identical normalized collection skips re-embedding.

## Operational Behavior

`probe-doc source=gt-academic-calendar` remains a single, free request and verifies that the page advertises a current academic year. `sync-doc source=gt-academic-calendar` adds one JSON request, then embeds only when normalized content changed. No LLM is used for fetching or parsing.
