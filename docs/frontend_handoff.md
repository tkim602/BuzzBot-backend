# BuzzBot Web Handoff

## Connection contract

- Backend base URL: deployment-provided; local default `http://localhost:8000`.
- Chat: `POST /chat`, JSON request and response documented in [api.md](api.md).
- Readiness: `GET /live` for process health and `GET /ready` for dependency readiness.
- Streaming: not implemented; wait for one JSON response per request.

`thread_id` is an optional conversation key, not authentication. The web client may generate a
bounded opaque ID matching `^[A-Za-z0-9_.:-]+$` and retain it for a browser conversation.

Firebase authentication is optional. Immediately before each authenticated request, obtain the
current Firebase ID token and send `Authorization: Bearer <id-token>`. Anonymous requests send no
Authorization header. On an authenticated `401`, force-refresh once and retry once; never store,
log, or place the token in conversation state.

## Rendering

- Render `answer` as untrusted text.
- Render every citation from `citations[]` with its exact `title`, `url`, `quote`, optional `page`,
  and `fetched_at`. Do not synthesize citation URLs client-side.
- Use non-null `freshness.as_of` as the conservative evidence timestamp; do not replace null with
  the current time.
- Treat non-empty `notes` as abstention/qualification messages, not hidden diagnostics.
- `debug` is optional and normally null outside explicitly enabled development diagnostics.

## Errors

- `422`: show an input validation message.
- `429`: show `detail.message`; respect `detail.retry_after_seconds` when present.
- `503` from `/ready`: backend dependencies are unavailable; do not send chat requests.
- Other failures: show a retry action and retain `X-Request-ID` for diagnostics.

## Browser integration

The backend uses the explicit `CORS_ORIGINS` allowlist and never accepts wildcard origins with
credentials. `Authorization` is allowed through the existing CORS middleware.

Web environment variable:

```text
NEXT_PUBLIC_BUZZBOT_API_URL=http://localhost:8000
```

Use the web framework's public/server variable naming rules when that repository is implemented.

## Current persistence boundary

Firebase provides browser identity, while conversations remain same-browser localStorage scoped by
Firebase UID. Authentication does not provide cross-device history or server-side message storage.
Cloud deployment and an external production ingestion scheduler remain separate operational work.
