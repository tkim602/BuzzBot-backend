# BuzzBot v2

BuzzBot is a citation-first, controlled Agentic RAG assistant for Georgia Tech students. It combines
validated OSCAR schedule data with a controlled registry of official GT documents, then orchestrates the
retrieval paths through an explicit LangGraph workflow.

The project is designed to answer questions such as:

- Is `CS 7650` offered in Fall 2026? What are its CRN, instructor, meeting time, and room?
- What does the official Catalog say about a course's credits or prerequisites?
- What is the official registration, add/drop, or withdrawal date for a term?
- What do official GT or OMSCS admissions pages require?

It does not register, add, or drop courses and never accesses a student's authenticated account.

## Architecture

```mermaid
flowchart LR
    User --> API[FastAPI /v2/chat]
    API --> Graph[Controlled LangGraph]
    Graph --> Understand[Understand + validate fields]
    Understand -->|schedule| SQL[Published OSCAR SQL]
    Understand -->|catalog/calendar/policy| Hybrid[pgvector + FTS + RRF]
    SQL --> Evidence[Typed evidence]
    Hybrid --> Evidence
    Evidence --> Gate{Evidence valid?}
    Gate -->|no| Retry[One bounded retry]
    Retry --> Gate
    Gate -->|yes| Answer[Deterministic or gpt-4o-mini answer]
    Answer --> Citation{Citation grounded?}
    Citation -->|yes| Response[Answer + official citations]
    Citation -->|no| Abstain[Transparent abstention]
    Graph -. optional checkpoints .-> Postgres[(PostgreSQL + pgvector)]
```

Two data planes are intentionally separate:

1. Schedule facts are normalized from public OSCAR pages, validated, and atomically published as a
   version. Queries read only the latest `PUBLISHED` version.
2. Policies and course descriptions come from a controlled official-source registry. Retrieval uses
   vector search and PostgreSQL full-text search fused with RRF.

See [docs/architecture.md](docs/architecture.md) for the detailed flow and failure gates.

## Safety and cost boundaries

- Probe a representative URL before a bounded source sync.
- Source-specific discovery creates an immutable URL manifest and fails closed above its configured
  safety ceiling.
- No wildcard Georgia Tech crawl, Common Crawl fallback, or authenticated OSCAR flow in v2.
- Auth redirects, 429 responses, external redirects, and incompatible bodies stop that source.
- Identical document hashes skip re-embedding; authority changes update metadata only.
- The application clamps tracked API usage to `$3.00` and blocks new calls after the cap.
- `LANGSMITH_TRACING=false` by default, so development tracing has no LangSmith usage.
- The default models are `gpt-4o-mini` and `text-embedding-3-small`.

The `$3` guard is application-level. Configure an account/project budget in the provider dashboard as
an additional account-wide billing boundary.

## Quickstart

Requirements: Python 3.11+, Docker, and an OpenAI API key for embedding/document-answer operations.
Schedule SQL queries and the offline evaluation do not call OpenAI.

```bash
cp .env.example .env
# Add OPENAI_API_KEY only in .env

make setup
make db-up
make migrate
make test
make test-db
make run-backend
```

The API starts at `http://localhost:8000`.

```bash
curl -s http://localhost:8000/live
curl -s http://localhost:8000/ready

curl -s http://localhost:8000/v2/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Is CS 7650 offered in Fall 2026?",
    "thread_id": "portfolio-demo"
  }'
```

`thread_id` is optional. When supplied, LangGraph checkpoints compact graph state in PostgreSQL; it
is not a GT identity and must contain only letters, numbers, `.`, `_`, `:`, or `-`.

## Controlled data collection

The official document registry currently contains Registrar policy, Academic Calendar, Catalog,
OMSCS, and Undergraduate Admissions seeds. Probe first, then sync only a READY source:

```bash
make probe-doc source=gt-catalog
make sync-doc source=gt-catalog
```

Sync one OSCAR subject only after choosing one representative course for the gate probe:

```bash
make sync-oscar term=202608 subject=CS course=7650
```

The sync saves a safe snapshot, normalizes courses/sections/meetings, checks completeness and
freshness, and publishes atomically. A failed collection never replaces the last good version.

### Clean `buzzbot_v2` database

`DATABASE_URL` is the only database setting. The application, Alembic, document ingestion,
OSCAR ingestion, LangGraph checkpoints, and audit scripts derive their async or sync driver from
that one URL.

For a first clean setup:

```bash
docker compose up -d db
docker compose exec db createdb -U buzzbot buzzbot_v2
# Set DATABASE_URL=postgresql+asyncpg://buzzbot:buzzbot_dev@localhost:5432/buzzbot_v2 in .env
make migrate

make probe-doc source=gt-registrar
make sync-doc source=gt-registrar
make sync-oscar term=202608 subject=CS course=7650

make run-backend
curl -s http://localhost:8000/ready
```

Probe each additional controlled document source before syncing it. Do not run a term-wide subject
loop until one representative subject has published successfully and the API is ready.

## Health semantics

- `GET /live`: process liveness only; it has no database or external dependency.
- `GET /ready`: requires the database, controlled official document chunks, a non-expired published
  schedule, and the PostgreSQL checkpointer when checkpointing is enabled.
- `GET /usage`: tracked OpenAI usage and remaining application budget.

LangSmith is deliberately not a readiness dependency.

## Evaluation

The small routing gate is deterministic and budget-free:

```bash
make eval-v2
```

It measures routing accuracy and required-field extraction across schedule, Catalog, Calendar, and
policy questions. Citation grounding, bounded retry, abstention, persistence state reset, atomic
publication, and retrieval source pinning are covered by unit and PostgreSQL integration tests.

The current frozen retrieval baseline uses the fixed `dev_100` manifest:

- Hit@5: 0.57
- MRR@5: 0.40017

Run `make quality-retrieval-dev` for the budget-free retrieval benchmark. `make quality-chat-dev`
calls the real `/v2/chat` contract and the configured judge model, so run it intentionally under the
shared `$3` application budget.

## Project layout

```text
app/graph/                 LangGraph state, understanding, workflow, checkpoint adapter
app/retrieval/             Typed schedule and official-document retrieval tools
app/api/                   v2 agent API and live/ready/usage endpoints
ingestion/schedule/        OSCAR probe, normalization, validation, atomic publication
ingestion/documents/       Controlled registry, probe, changed-only document sync
db/                        SQLAlchemy models and Alembic migrations
eval/                      Offline v2 golden set and evaluation runner
tests/                     Unit and PostgreSQL integration tests
docs/superpowers/          Final architecture designs and verification reports
```

## Verification

```bash
PYTHONPATH=$PWD pytest -q
RUN_DB_TESTS=1 PYTHONPATH=$PWD pytest -q tests/integration
ruff check app/graph app/retrieval app/api/agent.py
mypy --follow-imports=skip app/graph app/retrieval/tools.py app/api/agent.py
git diff --check
```

Final implementation decisions and verification evidence are recorded under `docs/superpowers/`.
