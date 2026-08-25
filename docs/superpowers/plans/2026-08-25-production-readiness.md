# Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the existing BuzzBot backend and web client at their trust, freshness, readiness, CI, and runtime boundaries without changing retrieval or ingestion behavior.

**Architecture:** FastAPI resolves an optional verified Firebase identity once per request and uses that identity for guardrails and checkpoint namespaces; anonymous users keep a bounded IP/User-Agent fingerprint whose proxy input is trusted only by configuration. Chat freshness is derived from returned evidence, while readiness uses existing ingestion manifests in strict mode. The Next.js client asks Firebase for a current ID token immediately before chat calls and preserves request IDs on failures.

**Tech Stack:** FastAPI, Pydantic Settings, Firebase Admin SDK, SQLAlchemy/PostgreSQL/pgvector, LangGraph, pytest, Next.js 16, Firebase Web SDK, Vitest, Playwright, GitHub Actions, Docker.

---

### Task 1: Evidence-derived freshness and debug hygiene

**Files:**
- Create: `app/api/freshness.py`
- Modify: `app/api/routes/chat.py`
- Modify: `app/api/schemas/chat.py`
- Modify: `app/core/config.py`
- Test: `tests/test_chat_freshness.py`
- Test: `tests/test_agent_api.py`

- [ ] Write failing tests for old schedule evidence, old document evidence, missing timestamps, and mixed evidence choosing the oldest timestamp.
- [ ] Run `python3 -m pytest -q tests/test_chat_freshness.py tests/test_agent_api.py` and confirm the response-time freshness behavior fails.
- [ ] Implement a pure aggregate helper that parses evidence `fetched_at`, returns `None` when no valid timestamp exists, and conservatively returns the oldest timestamp for mixed evidence.
- [ ] Make `DebugInfo` optional and return it only when `CHAT_DEBUG_RESPONSES=true`.
- [ ] Re-run focused tests and commit `fix: derive chat freshness from evidence`.

### Task 2: Optional Firebase request identity

**Files:**
- Create: `app/core/auth.py`
- Modify: `app/api/routes/chat.py`
- Modify: `app/core/config.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Test: `tests/test_auth.py`
- Test: `tests/test_agent_api.py`

- [ ] Write failing tests for anonymous identity, verified GT identity, verified non-GT identity, invalid bearer tokens, and spoofed request JSON.
- [ ] Run the focused tests and confirm the missing auth dependency fails.
- [ ] Add a lazy Firebase Admin verifier behind one FastAPI dependency; tests override the verifier and never contact Firebase.
- [ ] Derive `gatech_eligible` only from verified token claims and reject malformed/invalid/expired bearer tokens with 401.
- [ ] Re-run focused tests and commit `feat: verify optional Firebase identity`.

### Task 3: Identity isolation and trusted proxy policy

**Files:**
- Modify: `app/core/guardrails.py`
- Modify: `app/api/routes/chat.py`
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Test: `tests/test_guardrails.py`
- Test: `tests/test_agent_api.py`

- [ ] Write failing tests proving authenticated UID isolation, stable same-UID namespace, anonymous fallback, untrusted forwarded-header rejection, trusted first-hop parsing, and malformed-chain fallback.
- [ ] Run focused tests and confirm current unconditional `X-Forwarded-For` behavior fails.
- [ ] Pass the verified identity into the existing guardrail function; use `uid:<uid>` for authenticated clients and the existing hash for anonymous clients.
- [ ] Gate `X-Forwarded-For` behind `TRUST_PROXY_HEADERS=false` by default and accept only syntactically valid IP addresses.
- [ ] Use the same client ID for rate limits, duplicate cooldown, and LangGraph checkpoint namespace.
- [ ] Re-run focused tests and commit `fix: isolate client identity and proxy trust`.

### Task 4: Readiness completeness and operator endpoints

**Files:**
- Modify: `app/api/routes/health.py`
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Test: `tests/test_health.py`
- Test: `tests/test_operator_security.py`

- [ ] Write failing tests for local non-strict readiness, completed active-term manifest, partial/failed manifest, stale schedule, missing documents, and optional operator-token protection.
- [ ] Run focused tests and confirm current existence-only readiness fails.
- [ ] In strict mode require a completed `public-oscar` all-subject run for the active term and configurable minimum official document count; keep non-strict development behavior.
- [ ] Return structured checks for DB, checkpoint, document coverage/completeness, schedule publication/completeness/freshness.
- [ ] Protect `/usage`, `/stats`, and OpenAPI/docs with an optional environment operator token in production; preserve open local defaults.
- [ ] Re-run focused tests and commit `feat: enforce configurable production readiness`.

### Task 5: Frontend token and request-ID propagation

**Files (BuzzBot-web sibling worktree):**
- Modify: `src/components/buzzbot/auth.tsx`
- Modify: `src/components/buzzbot/chat-api.ts`
- Modify: `src/components/buzzbot/BuzzBotApp.tsx`
- Modify: `tests/chat-api.test.ts`
- Modify: `tests/buzzbot-app.test.tsx`

- [ ] Write failing tests for no anonymous Authorization header, current token use, one forced-refresh retry on 401, token failure, and request-ID preservation.
- [ ] Run focused Vitest tests and confirm failures.
- [ ] Expose a `getIdToken(forceRefresh?)` action from the existing auth provider and pass it to `sendChat` without storing or logging tokens.
- [ ] Extend `ChatApiError` with status/request ID; retry exactly once after authenticated 401 and show a compact reference only for unexpected errors.
- [ ] Assert stored conversation JSON contains no token.
- [ ] Re-run focused tests and commit `feat: authenticate chat requests`.

### Task 6: CI and Docker hardening

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Create: `tests/test_runtime_contracts.py`
- Create in BuzzBot-web: `.github/workflows/ci.yml`

- [ ] Add backend CI pgvector service, empty-DB Alembic migration, unit and DB integration gates, Ruff checks, and no paid/live commands.
- [ ] Add frontend fast and Playwright jobs using `npm ci` and `npx playwright install --with-deps chromium`, with no Firebase/backend credentials.
- [ ] Add static contract tests for CI commands and Docker non-root/runtime invariants.
- [ ] Convert the backend image to a builder/runtime layout, install runtime dependencies only, create a non-root user, and set writable Hugging Face cache directories.
- [ ] Run focused contract tests and commit `ci: gate production runtime contracts` in each repository.

### Task 7: Real-stack contract harness and documentation

**Files:**
- Create: `app/api/testing.py` only if needed for a deterministic provider override
- Create: `tests/integration/test_chat_contract.py`
- Modify: `README.md`
- Modify: `docs/api.md`
- Modify: `docs/frontend_handoff.md`
- Modify: `docs/architecture.md`
- Modify in BuzzBot-web: `README.md`

- [ ] Add the smallest backend contract test that runs FastAPI against PostgreSQL with graph/provider behavior stubbed at the service seam, covering anonymous chat, authenticated identity, and request ID headers.
- [ ] Do not add a second E2E framework; document the exact local Next.js → FastAPI → PostgreSQL command when full Playwright orchestration would duplicate existing CI.
- [ ] Document freshness, auth, identity, proxy, readiness, operator endpoints, local-history limitations, CI, Docker, and remaining external Firebase/deployment requirements.
- [ ] Run backend `ruff check .`, `ruff format --check .`, unit tests, DB tests, Alembic on an empty DB when Docker is available, and `git diff --check`.
- [ ] Run frontend `npm run verify`, `npm run test:e2e`, and `git diff --check`.
- [ ] Commit `docs: document production hardening` in each repository and produce the final readiness report.
