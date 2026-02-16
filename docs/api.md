# API Reference

Base URL: `http://localhost:8000`

## POST /chat

Main chat endpoint. Accepts a user query and returns a citation-backed answer.

### Request

```json
{
  "query": "When is the registration deadline for Fall 2025?",
  "user_context": {
    "term": "Fall 2025",
    "major": "CS"
  },
  "rmp_excerpt": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | User question (1-2000 chars) |
| `user_context` | object | No | Optional context (term, major) |
| `rmp_excerpt` | string | No | User-provided RMP text (max 5000 chars) |

### Response

```json
{
  "answer": "The registration deadline for Fall 2025 is...",
  "citations": [
    {
      "url": "https://registrar.gatech.edu/calendar",
      "title": "Academic Calendar",
      "fetched_at": "2025-08-01T12:00:00Z",
      "quote": "Registration for Fall 2025 closes on..."
    }
  ],
  "confidence": 0.85,
  "freshness": {
    "strategy": "live_fetch",
    "as_of": "2025-08-15T10:30:00Z"
  },
  "notes": [],
  "debug": {
    "intent": "registrar_calendar",
    "live_fetch_used": true,
    "retrieval_top_k": 6
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `answer` | string | The generated answer |
| `citations` | array | Source citations with URL, title, date, quote |
| `confidence` | float | 0.0-1.0 confidence score |
| `freshness.strategy` | string | `indexed`, `live_fetch`, or `hybrid` |
| `freshness.as_of` | string | ISO8601 timestamp of response |
| `notes` | array | Warnings, caveats |
| `debug.intent` | string | Classified query intent |
| `debug.live_fetch_used` | boolean | Whether live fetch was triggered |
| `debug.retrieval_top_k` | int | Number of chunks retrieved |

## GET /health

Health check endpoint.

```json
{"status": "ok", "service": "buzzbot"}
```

## GET /stats

Basic ingestion and index statistics.

```json
{
  "sources": 2,
  "documents": 45,
  "chunks": 312
}
```

## Error Responses

All errors return JSON with a `detail` field:

```json
{"detail": "Error description"}
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request (invalid query) |
| 422 | Validation error |
| 500 | Internal server error |
