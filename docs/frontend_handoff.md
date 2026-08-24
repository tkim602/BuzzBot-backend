# BuzzBot Web Handoff

## Connection contract

- Backend base URL: deployment-provided; local default `http://localhost:8000`.
- Chat: `POST /chat`, JSON request and response documented in [api.md](api.md).
- Readiness: `GET /live` for process health and `GET /ready` for dependency readiness.
- Streaming: not implemented; wait for one JSON response per request.

`thread_id` is an optional conversation key, not authentication. The web client may generate a
bounded opaque ID matching `^[A-Za-z0-9_.:-]+$` and retain it for a browser conversation.

## Rendering

- Render `answer` as untrusted text.
- Render every citation from `citations[]` with its exact `title`, `url`, `quote`, optional `page`,
  and `fetched_at`. Do not synthesize citation URLs client-side.
- Use `freshness.as_of` as the displayed data timestamp.
- Treat non-empty `notes` as abstention/qualification messages, not hidden diagnostics.
- `debug` is useful during MVP integration but is not a stable presentation model.

## Errors

- `422`: show an input validation message.
- `429`: show `detail.message`; respect `detail.retry_after_seconds` when present.
- `503` from `/ready`: backend dependencies are unavailable; do not send chat requests.
- Other failures: show a retry action and retain `X-Request-ID` for diagnostics.

## Local browser integration

The backend does not currently install permissive CORS middleware. Prefer a same-origin web proxy
during local and production deployment. If separate browser origins are required later, add an
explicit environment-driven allowlist; never use wildcard credentials.

Expected future web environment variable:

```text
BUZZBOT_API_BASE_URL=http://localhost:8000
```

Use the web framework's public/server variable naming rules when that repository is implemented.

## Intentionally missing product work

1. Authentication and user identity
2. Conversation/message persistence
3. Streaming chat transport and UI
4. Citation presentation
5. Course/schedule result components
6. Backend and web deployment
7. External scheduled-ingestion deployment
