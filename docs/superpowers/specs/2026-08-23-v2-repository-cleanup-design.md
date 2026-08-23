# BuzzBot v2 Repository Cleanup Design

## Goal

Make the default repository tree represent the BuzzBot v2 product only. Preserve the working
LangGraph API, controlled ingestion, database migrations, quality evaluation, and their tests while
removing the retired v1 application and superseded development artifacts.

Git history is not rewritten. The cleanup changes only the current tree.

## Source of truth

The cleanup starts from `data/dev100-diagnosis`, which contains the latest verified v2 runtime and
quality improvements. The dirty root checkout and its uncommitted crawler experiment are outside
this change and must remain untouched. Untracked evaluation reports are local artifacts and must
not be deleted or committed.

## Keep

- FastAPI health endpoints and `POST /v2/chat`
- Controlled LangGraph workflow and checkpoint persistence
- Shared RAG answer, grounding, routing, embedding, and retrieval code used by v2
- Structured OSCAR ingestion and controlled official-document ingestion
- PostgreSQL models and Alembic migrations
- Current deterministic and live quality evaluation package, frozen datasets, manifests, and
  baselines
- Tests for retained production, ingestion, and evaluation behavior
- Current architecture, API, ingestion, source, and probe documentation
- Final architecture/design specifications and final verification reports

## Remove

- The retired Next.js frontend and its build/configuration commands
- Legacy `POST /chat` and runtime code used only by that endpoint
- Legacy live-fetch and user-provided RateMyProfessors paths
- Common Crawl and superseded sitemap/scheduler/calendar ingestion entry points
- The obsolete nightly workflow that invokes legacy ingestion
- Superseded root-level evaluation scripts, generated results, samples, and rubrics
- Claude-specific repository instructions and skills
- Superseded implementation plans, study guides, UI documentation, and intermediate reports

Shared modules are retained when v2 imports them even if they originated in v1. Removal is based on
the actual runtime/test dependency graph, not directory names.

## Required updates

- Remove retired routers from the FastAPI application.
- Narrow request/response schemas to the v2 contract without changing `/v2/chat` behavior.
- Remove stale Make targets, dependencies, Ruff exceptions, documentation links, and tests that
  exist only for deleted code.
- Ignore local generated quality-report directories so future evaluations do not clutter Git status.
- Keep CI focused on lint and the retained test suite.

## Verification

The cleaned tree must pass:

1. import/reference scan for deleted modules and paths;
2. the full non-live test suite;
3. PostgreSQL integration tests;
4. Ruff lint and format checks;
5. `git diff --check` and a secret scan of changed files;
6. FastAPI route inspection proving `/v2/chat`, health, stats, and usage remain while legacy
   `POST /chat` is absent.

No live ingestion, paid evaluation, database deletion, Git history rewrite, push, or merge is part
of this cleanup implementation.
