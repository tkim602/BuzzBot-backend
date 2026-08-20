# BuzzBot v2 Final Implementation Report

## Delivered

- `$3.00` application usage ceiling, public reset/set-limit removal, conservative unknown-model cost
  accounting, and development LangSmith tracing disabled.
- Probe-first public OSCAR collection with stored safe snapshots, normalization, validation gates,
  atomic publication, per-unit concurrency locks, and typed SQL retrieval.
- Controlled official GT document registry, one-request probes, changed-only sync, canonical metadata,
  and hybrid vector + FTS + RRF retrieval.
- Typed read-only tools for schedule, course details, Academic Calendar, and official policies.
- Explicit LangGraph workflow: understand, retrieve, validate, one retry, answer, citation validate,
  abstain.
- PostgreSQL checkpoint lifecycle with client namespace isolation.
- FastAPI `/v2/chat`, `/live`, `/ready`, `/usage`, offline eval, Docker healthcheck, and current v2
  documentation.

## Verification evidence

- Full pytest: `175 passed, 9 skipped`.
- PostgreSQL integration: `9 passed`.
- Focused Ruff: clean for Phase 3-5 changed Python files.
- Focused mypy: clean for graph, v2 API, health, retrieval tools, and eval.
- Offline v2 golden set: 12 cases, routing `1.00`, required-field extraction `1.00`.
- FastAPI/PostgreSQL smoke:
  - `/live`: 200
  - `/ready`: honest 503 because no non-expired published schedule
  - checkpoint: available
  - controlled document chunks: available
  - `/v2/chat` clarification path: 200, no paid API call
- Tracked OpenAI total after bounded ingestion/retrieval: `$0.00034198 / $3.00`.
- Secret-pattern scan: no pasted OpenAI or LangSmith keys in the worktree diff.

## Bounded live data result

- Official document plane is populated and retrieves Registrar/Catalog/Calendar evidence.
- The first bounded Fall 2026 OSCAR subject sync did not publish because the Banner course wildcard
  was initially omitted. The request-shape bug is fixed and regression-tested, but the source was not
  retried during the same bounded run.
- Readiness therefore fails closed until a representative subject is successfully published.

## Non-blocking environment limitations

- Docker build reached the base-image metadata pull and stalled in the current registry network; it
  was canceled after two minutes. Unit, PostgreSQL, API lifespan/checkpoint, and Dockerfile syntax
  paths were otherwise exercised locally.
- The host-wide Python installation contains unrelated legacy LangChain 0.3 packages that conflict
  with LangGraph 1.x's `langchain-core`. BuzzBot does not declare or import those legacy packages; a
  fresh project environment installs only the dependencies in `pyproject.toml`.

## Release state

- Branch: `codex/phase0-oscar-probe`
- Worktree: `.worktrees/phase0-oscar-probe`
- No merge, push, or pull request was performed.
