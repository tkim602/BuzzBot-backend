# BuzzBot Backend Productization Design

## Goal

Turn the current repository into the neutral `buzzbot-backend` product boundary without changing
retrieval, answer generation, ingestion semantics, quality thresholds, or fail-closed behavior.

## Scope

- Expose the production chat contract at `POST /chat`; keep `/live`, `/ready`, `/usage`, and the
  existing neutral operational endpoints.
- Rename active V2-era API symbols, commands, docs, and tracing tags to neutral BuzzBot names.
- Place API routes and schemas under `app/api`, database runtime code under `app/db`, and Alembic
  migrations at top-level `migrations`.
- Keep ingestion as explicit `python -m ingestion...` jobs, independent of FastAPI startup.
- Keep PR12 oracle and PR13 hierarchical retrieval code and results under `eval/` only.
- Make `DATABASE_URL` an explicit environment contract; local defaults belong in `.env.example`.
- Document the API contract, frontend handoff, current architecture, frozen quality baseline, and
  accepted Policy retrieval limitation.

## Non-goals

- No retrieval, reranking, prompt, answer, threshold, or benchmark changes.
- No production oracle or hierarchical retrieval.
- No frontend, authentication, account, conversation persistence, deployment, or DB-service work.
- No removal or rewriting of historical evaluation artifacts whose V2 wording is part of history.

## Boundaries

```text
FastAPI route -> existing LangGraph workflow -> RAG/retrieval/database

external scheduler -> ingestion CLI/job -> snapshot/normalize/validate/publish -> database
```

`app/rag` remains the RAG orchestration layer. `app/retrieval` remains the typed data-access layer;
renaming either would be a risky mechanical refactor with no product behavior benefit. Evaluation
experiments remain reproducible, but production modules must not import them.

## Compatibility

The retired versioned chat endpoint has no retained external consumer in this repository. It is removed rather than kept as
an undocumented alias. Active evaluation tooling is updated to call `/chat`; historical reports and
frozen identifiers remain unchanged.

## Verification

- Focused API, configuration, ingestion-boundary, and production-import tests.
- Full unit and PostgreSQL integration suites.
- Frozen Schedule SQL/NLU/renderer, Course Details, Calendar, and Policy retrieval checks using the
  repository's canonical commands; no paid chat/semantic evaluation.
- Ruff, format check, and `git diff --check`.
