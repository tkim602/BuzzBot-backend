# API Reference

Base URL: `http://localhost:8000`

## `POST /v2/chat`

Runs the controlled LangGraph workflow.

```json
{
  "query": "Is CS 7650 offered in Fall 2026?",
  "thread_id": "portfolio-demo",
  "user_context": {"term": "Fall 2026"},
  "history": []
}
```

`query` is required and limited to 2,000 characters. `thread_id` is optional, limited to 100
characters, and accepts only letters, numbers, `.`, `_`, `:`, and `-`. It is a conversation key, not
a GT user identity.

```json
{
  "thread_id": "portfolio-demo",
  "answer": "CS 7650 ... section A; CRN 12345 ...",
  "citations": [
    {
      "url": "https://oscar.gatech.edu/...",
      "title": "CS 7650 schedule",
      "fetched_at": "2026-08-20T00:00:00+00:00",
      "quote": "CS 7650 ... CRN 12345 ..."
    }
  ],
  "confidence": 0.95,
  "freshness": {"strategy": "langgraph_controlled", "as_of": "..."},
  "notes": [],
  "debug": {
    "intent": "course_schedule",
    "retrieval_top_k": 1,
    "top_sources": ["oscar"]
  }
}
```

Factual output without a grounded official citation is replaced by an abstention. The endpoint may
return `429` for request guardrails or the tracked API cost limit, and `422` for invalid input.

## Health and operations

- `GET /live`: dependency-free process liveness.
- `GET /ready`: DB, official chunks, published schedule freshness, and configured checkpoint status.
- `GET /stats`: source/document/chunk counts.
- `GET /usage`: tracked API cost, fixed maximum limit, and remaining budget.

`/ready` returns `200` when ready and `503` with individual check results otherwise. Usage mutation
is intentionally not exposed through the public API.
