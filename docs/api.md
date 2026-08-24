# BuzzBot Backend API

Local base URL: `http://localhost:8000`

## `POST /chat`

Runs the production LangGraph workflow. Content type is `application/json`; streaming is not
currently implemented.

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
    "as_of": "2026-08-24T00:00:00+00:00"
  },
  "notes": [],
  "debug": {
    "intent": "course_schedule",
    "source_filter": null,
    "retrieval_top_k": 1,
    "top_sources": ["oscar"],
    "rewritten_query": "Is CS 7650 offered in Fall 2026?",
    "current_term": "202608",
    "stage_timings_ms": {"total_ms": 125}
  }
}
```

`freshness.as_of` is the response data timestamp. Each citation URL is its verification URL;
`debug.top_sources` identifies the source used. An insufficient or ungrounded factual answer is
replaced by a fail-closed abstention and explanation in `notes`.

### Errors

- `422`: invalid request schema.
- `429`: request guardrail/rate limit or tracked API cost limit. JSON `detail.error` identifies
  `guardrail_violation` or `usage_limit_exceeded`; a retry delay may be present.
- `500`: unexpected server failure. Retain the request ID response header when reporting it.

Successful responses include `X-Request-ID`.

## Health and operations

- `GET /live`: dependency-free liveness; `200` while the API process responds.
- `GET /ready`: `200` only when DB, official chunks, schedule freshness, and the configured
  checkpoint store are ready; otherwise `503` with individual checks.
- `GET /usage`: tracked cost, configured limit, remaining budget, and usage percent.
- `GET /stats`: source/document/chunk counts.
- `GET /health`: retained liveness equivalent for existing operators.

No endpoint mutates usage, registration, or authenticated student data.
