# OSCAR Horizontal Expansion Verification

**Date:** 2026-08-20

**Branch:** `data/oscar-horizontal-expansion`

## Implemented

- PostgreSQL-backed immutable ingestion runs and ordered run units
- bounded single-process asyncio scheduling with per-unit isolation
- fixed-manifest resume and selected failed-unit retry
- global authentication stop
- global 429 scheduling pause, `Retry-After` handling, bounded retry, and resumable pause
- unit-scoped transient 5xx/transport retry
- public OSCAR subject discovery from the Banner `sel_subj` selector
- shared one-request post-probe subject collection
- full-term and explicit bounded-subject CLI modes

The existing atomic `term:subject` publication path is unchanged. Document ingestion was not
refactored, and no queue, universal provider base class, Redis, Celery, or n8n dependency was
added.

## Migration Verification

The configured `buzzbot_v2` database was upgraded to Alembic revision `004`.

An isolated empty PostgreSQL database was then created, migrated from no schema through revisions
`001`, `002`, `003`, and `004`, checked for the document, vector, schedule, and ingestion-run
tables, and deleted. Result:

```text
revision: 004
required tables checked: 5
missing: []
```

## Automated Verification

```text
PYTHONPATH=$PWD python3 -m pytest -q
221 passed, 10 skipped

RUN_DB_TESTS=1 PYTHONPATH=$PWD python3 -m pytest -q tests/integration
10 passed

Ruff changed-file format/check: passed
mypy ingestion/orchestration.py ingestion/schedule db/models.py: passed
git diff --check: passed
```

## Bounded Live OSCAR Verification

Only this explicit one-subject command was executed:

```bash
python3 -m ingestion.schedule.sync_term \
  --term 202608 \
  --probe-subject CS \
  --probe-course 7650 \
  --subjects CS \
  --concurrency 1
```

Network scope was bounded to one provider probe, one subject-discovery request, and one CS subject
request. No LLM or embedding API was called.

Observed manifest summary:

```json
{
  "run_id": "a17cfdfb-00e0-4db6-b252-6a4d6481beb5",
  "provider": "public-oscar",
  "scope": {"term": "202608", "selection": "explicit"},
  "status": "COMPLETED",
  "planned": 1,
  "succeeded": 1,
  "failed": 0,
  "remaining": 0,
  "complete": true,
  "stop_reason": null,
  "planned_units": ["CS"]
}
```

Observed CS unit result:

```json
{
  "requests_used": 1,
  "records_fetched": 1779,
  "records_parsed": 1779,
  "failures": 0,
  "courses": 178,
  "sections": 1779,
  "meetings": 1779,
  "version_status": "PUBLISHED"
}
```

## Operator Commands

Apply migrations:

```bash
make migrate
```

Start a fresh Fall 2026 all-subject run:

```bash
make sync-oscar-all term=202608
```

Expected final summary shape when every planned subject succeeds:

```json
{
  "run_id": "<uuid>",
  "provider": "public-oscar",
  "scope": {"term": "202608", "selection": "all"},
  "status": "COMPLETED",
  "planned": "<discovered subject count>",
  "succeeded": "<same count>",
  "failed": 0,
  "remaining": 0,
  "complete": true
}
```

If the run pauses, resume the same immutable manifest:

```bash
python3 -m ingestion.schedule.sync_term \
  --run-id <run-uuid> \
  --resume
```

Retry specific failed subjects without changing the manifest:

```bash
python3 -m ingestion.schedule.sync_term \
  --run-id <run-uuid> \
  --resume \
  --retry-failed ARCH,ECE
```

The full Fall 2026 all-subject command was intentionally not executed by Codex.
