# Phase 5 Production Surface and Evaluation Plan

**Goal:** Make the controlled LangGraph workflow usable through FastAPI, production-observable,
optionally durable in PostgreSQL, and demonstrably testable without spending API budget.

**Architecture:** Add a versioned `/v2/chat` endpoint while retaining the legacy endpoint for safe
comparison. Compile the graph per request with the request-scoped SQLAlchemy session and an optional
application-scoped PostgreSQL checkpointer. Liveness has no dependencies; readiness checks the
database, official documents, and a fresh published schedule. Evaluation defaults to deterministic
offline routing checks and requires an explicit flag for live API calls.

**Primary references:**
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/add-memory
- https://pypi.org/project/langgraph-checkpoint-postgres/

## Constraints

- PostgreSQL checkpoint setup is idempotent and failure leaves liveness available.
- A failed/disabled checkpointer never enables unbounded in-process memory.
- Checkpoint thread IDs are explicit bounded request fields; no student identity is stored.
- `/ready` fails closed if SQL, official document evidence, or a current/stale published schedule is
  unavailable. LangSmith is not a readiness dependency.
- `/v2/chat` retains the `$3` usage guard and existing request guardrails.
- Offline eval is the default and makes no OpenAI, LangSmith, crawl, or network call.
- Documentation must describe the implemented controlled registry, not the removed broad crawler.

## Task 1: Optional PostgreSQL Checkpointing

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/core/config.py`
- Create: `app/graph/persistence.py`
- Test: `tests/test_graph_persistence.py`

- [ ] Add the official PostgreSQL checkpointer and psycopg pool dependencies.
- [ ] Convert the configured SQL URL to a psycopg-compatible URI without exposing credentials.
- [ ] Add an async lifecycle context that calls setup once and closes cleanly.
- [ ] Clear stale per-thread optional state during each understand step.

## Task 2: Versioned Agent API and Health Gates

**Files:**
- Create: `app/api/agent.py`
- Modify: `app/api/health.py`
- Modify: `app/main.py`
- Modify: `app/schemas/chat.py`
- Test: `tests/test_agent_api.py`
- Test: `tests/test_health.py`

- [ ] Add `/v2/chat` with optional bounded `thread_id`, graph invocation, response mapping, and the
  existing usage/rate/concurrency guards.
- [ ] Add dependency-free `/live` and database/data-aware `/ready`.
- [ ] Make checkpointer startup best-effort and expose its availability in readiness.

## Task 3: Budget-Free Eval and Portfolio Documentation

**Files:**
- Create: `eval/agentic_rag_golden.jsonl`
- Create: `eval/agentic_rag_eval.py`
- Test: `tests/test_agentic_eval.py`
- Create: `.env.example`
- Rewrite: `README.md`
- Modify: `docs/architecture.md`

- [ ] Score intent routing and required query fields offline by default.
- [ ] Document how to run tests, bounded probes/sync, v2 API, and optional paid smoke tests.
- [ ] Document the explicit graph, structured-vs-document data paths, citations, retry ceiling,
  readiness, checkpointer, `$3` cap, and current known data limitations.

## Verification

```bash
PYTHONPATH=$PWD pytest -q
RUN_DB_TESTS=1 PYTHONPATH=$PWD pytest -q tests/integration
ruff check <changed files>
mypy --follow-imports=skip app/graph/persistence.py app/api/agent.py
python3 eval/agentic_rag_eval.py
git diff --check
```
