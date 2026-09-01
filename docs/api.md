# BuzzBot Backend API

Local base URL: `http://localhost:8000`

## `POST /chat`

Runs the production LangGraph workflow. Content type is `application/json`; streaming is not
currently implemented.

Authentication is optional. Anonymous requests omit `Authorization`. Authenticated clients send a
Firebase ID token as `Authorization: Bearer <id-token>`. The backend verifies the token and derives
UID, email verification, and `@gatech.edu` eligibility from verified claims; request JSON is never
trusted as identity.

### Request

```json
{
  "query": "Is CS 7650 offered in Fall 2026?",
  "thread_id": "portfolio-demo",
  "user_context": {"term": "Fall 2026", "major": null},
  "history": [
    {"role": "user", "content": "Tell me about CS 7650."},
    {"role": "assistant", "content": "What term are you interested in?"}
  ]
}
```

- `query`: required, 1–2,000 characters.
- `thread_id`: optional conversation key, 1–100 characters, limited to letters, numbers, `.`, `_`,
  `:`, and `-`. It is not a Georgia Tech identity.
- `user_context`: optional current `term` and `major` hints.
- `history`: optional, at most 20 non-empty user/assistant turns.

### Response

```json
{
  "thread_id": "portfolio-demo",
  "answer": "CS 7650 is offered in Fall 2026 ...",
  "citations": [
    {
      "url": "https://oscar.gatech.edu/...",
      "title": "CS 7650 schedule",
      "fetched_at": "2026-08-20T00:00:00+00:00",
      "quote": "CS 7650 ... CRN 12345 ...",
      "page": null
    }
  ],
  "confidence": 0.95,
  "freshness": {
    "strategy": "langgraph_controlled",
    "as_of": "2026-08-20T00:00:00+00:00"
  },
  "notes": [],
  "debug": null
}
```

`freshness.as_of` is evidence-derived, never response time. Schedule evidence uses its published
OSCAR snapshot timestamp and document evidence uses the retrieved chunk timestamp. Multiple
evidence items use the oldest timestamp; if any used evidence lacks a valid timezone-aware
timestamp, `as_of` is `null`. Citation-specific `fetched_at` remains unchanged. `debug` is optional
and is returned only when `CHAT_DEBUG_RESPONSES=true`. An insufficient or ungrounded factual answer
is replaced by a fail-closed abstention and explanation in `notes`.

### Errors

- `422`: invalid request schema.
- `401`: malformed, invalid, revoked (when configured), or expired Firebase bearer token.
- `429`: request guardrail/rate limit or tracked API cost limit. JSON `detail.error` identifies
  `guardrail_violation` or `usage_limit_exceeded`; a retry delay may be present.
- `500`: unexpected server failure. Retain the request ID response header when reporting it.

Successful responses include `X-Request-ID`.

## Health and operations

- `GET /live`: dependency-free liveness; `200` while the API process responds.
- `GET /ready`: in local non-strict mode, checks DB, official documents, active-term schedule
  freshness, and checkpoint availability. With `READINESS_STRICT=true`, it also requires the
  configured minimum official-document count and a completed all-subject ingestion manifest for
  `ACTIVE_TERM_CODE`; otherwise it returns `503` with structured checks.
- `GET /usage`: tracked cost, configured limit, remaining budget, and usage percent.
- `GET /stats`: source/document/chunk counts.
- `GET /health`: retained liveness equivalent for existing operators.

`/usage` and `/stats` require `X-Operator-Token` when `OPERATOR_API_TOKEN` is set and fail closed in
production when it is unset. Interactive API docs are disabled in production unless
`API_DOCS_ENABLED=true`. No endpoint mutates usage, registration, or authenticated student data.
