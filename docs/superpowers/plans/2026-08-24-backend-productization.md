# BuzzBot Backend Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Productize the repository as the neutral BuzzBot backend while preserving all accepted runtime and quality behavior.

**Architecture:** Keep the existing FastAPI -> LangGraph -> RAG/retrieval/database flow and the independently executable ingestion CLIs. Limit structural moves to API ownership, database runtime ownership, and top-level migrations; keep rejected retrieval experiments isolated in `eval/`.

**Tech Stack:** Python 3.11, FastAPI, LangGraph, SQLAlchemy, PostgreSQL/pgvector, Alembic, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-24-backend-productization-design.md`

## Global Constraints

- Do not tune retrieval, answers, prompts, thresholds, or benchmarks.
- Preserve fail-closed grounding, citations, source allowlists, ingestion publication semantics, and frozen artifacts.
- Do not add dependencies or frontend/auth/account features.
- Preserve unrelated user changes by working from current accepted `origin/main` in an isolated worktree.

---

### Task 1: Neutral API contract

**Files:**
- Move: `app/api/agent.py` -> `app/api/routes/chat.py`
- Move: `app/api/health.py` -> `app/api/routes/health.py`
- Move: `app/schemas/chat.py` -> `app/api/schemas/chat.py`
- Modify: `app/main.py`, `eval/quality/chat_runner.py`, `tests/test_agent_api.py`, `tests/test_chat_quality_eval.py`

**Interfaces:**
- Produces: `POST /chat`, `ChatRequest`, `ChatResponse`, and unchanged response fields.

- [ ] Add assertions that `/chat` exists, the retired versioned endpoint does not, and active evaluation calls `/chat`.
- [ ] Run focused tests and confirm they fail on the retired route.
- [ ] Move/rename the minimum active API files and symbols; update imports and tracing tag.
- [ ] Run focused tests and confirm they pass.

### Task 2: Backend ownership and portability

**Files:**
- Move: `db/models.py`, `db/session.py` -> `app/db/`
- Move: `db/migrations/` -> `migrations/`
- Modify: imports, `alembic.ini`, `migrations/env.py`, `pyproject.toml`, `app/core/config.py`
- Add/modify tests: `tests/test_database_config.py`, `tests/test_backend_boundaries.py`

**Interfaces:**
- Produces: `app.db.models`, `app.db.session`, top-level Alembic migrations, required `DATABASE_URL`.

- [ ] Add tests for explicit DB configuration, importable ingestion CLIs, migration location, and no production imports from evaluation modules.
- [ ] Run focused tests and confirm the new boundary assertions fail.
- [ ] Perform history-preserving moves and update imports/config only.
- [ ] Run focused tests and confirm they pass.

### Task 3: Active documentation and commands

**Files:**
- Modify: `README.md`, `docs/api.md`, `docs/architecture.md`, `docs/sources.md`, `docs/ingestion.md`, `eval/quality/README.md`, `.env.example`, `Makefile`
- Create: `docs/frontend_handoff.md`

**Interfaces:**
- Produces: frontend-readable `/chat` contract, independent ingestion commands, frozen baseline statement, and `make eval-routing`.

- [ ] Replace V2 wording only in active documentation and commands.
- [ ] Document response, error, readiness, CORS, configuration, non-streaming status, and next frontend tasks.
- [ ] Preserve historical reports, benchmark case IDs, model names, and crawler-v2 URLs.
- [ ] Search active surfaces and confirm obsolete product V2 naming is gone.

### Task 4: Verification and handoff

**Files:**
- Modify only defects introduced by Tasks 1-3.

**Interfaces:**
- Produces: reviewable branch with deterministic evidence and no paid evaluation.

- [ ] Run focused API/boundary tests.
- [ ] Run the full unit suite and PostgreSQL integration suite.
- [ ] Run canonical frozen Schedule SQL/NLU/renderer, Course Details, Calendar, and current Policy retrieval checks.
- [ ] Run Ruff, Ruff format check, and `git diff --check`.
- [ ] Inspect diff/stat, confirm frozen artifact hashes and retrieval production files are unchanged, then commit and push.
