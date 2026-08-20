# OSCAR Horizontal Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add resumable, rate-limit-aware Fall-term OSCAR subject orchestration while preserving the existing validated `term:subject` publication boundary.

**Architecture:** PostgreSQL stores one immutable run manifest and its unit rows. A small orchestration module schedules provider-agnostic unit callables, while an OSCAR adapter owns subject discovery and maps the existing single-subject collector into common outcomes. The document pipeline is unchanged.

**Tech Stack:** Python 3.11+, asyncio, httpx, lxml, SQLAlchemy 2, PostgreSQL, Alembic, pytest

---

## Repository Compatibility Findings

- `DataVersion.requested_unit` already uses `term:subject`; no publication schema redesign is needed.
- `publish_collection()` already preserves last-known-good versions and serializes same-unit publication.
- `sync_subject()` currently couples provider probing to one subject fetch. Extracting a shared post-probe collection path lets batch runs probe once without changing the single-subject command.
- No ingestion-run/checkpoint table exists; additive migration `004` is required.
- `ProbeHttpResponse.retry_after` and auth redirect classification already exist and should be reused.
- No queue or scheduler dependency is required. One asyncio process and PostgreSQL state satisfy the approved scope.
- Document sources already dispatch on source type. They are outside this implementation because no document batch requirement exists yet.

### Task 1: Persist immutable run manifests

**Files:**
- Modify: `db/models.py`
- Create: `db/migrations/versions/004_ingestion_runs.py`
- Modify: `tests/test_schedule_models.py`
- Create: `tests/integration/test_ingestion_runs.py`

- [ ] **Step 1: Write failing model and migration tests**

Assert that `IngestionRun` and `IngestionRunUnit` expose the approved columns, status checks, unique `(run_id, unit_key)` and `(run_id, position)` constraints, and cascade deletion from run to units. The PostgreSQL integration test inserts one run with two ordered units and proves duplicate units are rejected.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_schedule_models.py \
  tests/integration/test_ingestion_runs.py
```

Expected: collection/import failure because the models and migration do not exist.

- [ ] **Step 3: Add the minimal schema**

Add two models and migration tables:

```python
class IngestionRun(Base):
    id: UUID
    provider: str
    scope_json: dict
    status: str
    stop_reason: str | None
    concurrency: int
    retry_limit: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

class IngestionRunUnit(Base):
    id: UUID
    run_id: UUID
    unit_key: str
    position: int
    status: str
    attempts: int
    result_json: dict
    published_version_id: UUID | None
```

Use database checks for run/unit statuses, positive concurrency, nonnegative retries/attempts, unique unit ordering, and `ON DELETE CASCADE`. Do not add relationships that execution code does not use.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_schedule_models.py
RUN_DB_TESTS=1 PYTHONPATH=$PWD python3 -m pytest -q tests/integration/test_ingestion_runs.py
```

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add db/models.py db/migrations/versions/004_ingestion_runs.py \
  tests/test_schedule_models.py tests/integration/test_ingestion_runs.py
git -c user.name=tkim602 -c user.email=tkim602@gatech.edu \
  commit -m "feat: persist ingestion run manifests"
```

### Task 2: Add the reusable orchestration kernel

**Files:**
- Create: `ingestion/orchestration.py`
- Create: `tests/test_orchestration.py`
- Modify: `tests/integration/test_ingestion_runs.py`

- [ ] **Step 1: Write failing manifest lifecycle tests**

Define tests for:

```python
run_id = create_run(session_factory, "public-oscar", {"term": "202608"}, 2, 2)
plan_run(session_factory, run_id, ("AE", "CS"))
summary = load_run_summary(session_factory, run_id)
```

Assert deterministic ordering, one-time planning, distinct run IDs, exact coverage reconciliation, crash recovery from `RUNNING` to `PENDING`, and selected failed-unit reset.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_orchestration.py
```

Expected: import failure for `ingestion.orchestration`.

- [ ] **Step 3: Implement minimal run state operations**

Create only the shared types and functions required by the runner:

```python
class UnitOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FAILED = "FAILED"

@dataclass(frozen=True)
class UnitResult:
    outcome: UnitOutcome
    summary: dict[str, object]
    retry_after_seconds: int | None = None
    published_version_id: UUID | None = None
    reason: str | None = None
```

Keep SQL state helpers in the same module until a second caller proves a repository split useful.

- [ ] **Step 4: Write failing scheduler behavior tests**

Use async callables and an injected sleep function to prove:

- concurrency never exceeds the configured limit
- deterministic failure does not stop other units
- a recoverable 429 pauses new scheduling and retries
- exhausted 429 retries leave the run `PAUSED` with the unit pending
- auth failure stops new scheduling and marks the run `FAILED`
- resume uses only stored pending units and does not rediscover

- [ ] **Step 5: Run scheduler tests and verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_orchestration.py
```

Expected: lifecycle tests pass and scheduler tests fail because `run_batch()` is absent.

- [ ] **Step 6: Implement bounded asyncio scheduling**

Implement:

```python
async def run_batch(
    run_id: UUID,
    session_factory,
    run_unit,
    *,
    sleep=asyncio.sleep,
) -> RunSummary:
    ...
```

Schedule at most the stored concurrency, persist `RUNNING` before starting a task, process `asyncio.wait(..., FIRST_COMPLETED)`, and make 429 waits interruptible. Use `Retry-After` when valid; otherwise use capped exponential backoff with small stdlib `random.uniform()` jitter. Do not hold a DB transaction across network calls or sleep.

- [ ] **Step 7: Run focused tests and verify GREEN**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_orchestration.py
```

Expected: all orchestration tests pass.

- [ ] **Step 8: Commit**

```bash
git add ingestion/orchestration.py tests/test_orchestration.py \
  tests/integration/test_ingestion_runs.py
git -c user.name=tkim602 -c user.email=tkim602@gatech.edu \
  commit -m "feat: orchestrate resumable ingestion units"
```

### Task 3: Add OSCAR discovery and a reusable post-probe collector

**Files:**
- Create: `ingestion/schedule/oscar.py`
- Modify: `ingestion/schedule/sync.py`
- Create: `tests/test_oscar_adapter.py`
- Modify: `tests/test_schedule_sync.py`
- Create: `tests/fixtures/oscar_subjects_sample.html`

- [ ] **Step 1: Write failing discovery tests**

Test the public Banner discovery URL:

```text
https://oscar.gatech.edu/bprod/bwckgens.p_proc_term_date
  ?p_calling_proc=bwckschd.p_disp_dyn_sched
  &p_term=202608
```

The parser must read `select[name=sel_subj]`, reject malformed/empty pages, exclude `%`, deduplicate, validate subject codes, and return deterministic source order.

- [ ] **Step 2: Run discovery tests and verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_oscar_adapter.py
```

Expected: import failure for the OSCAR schedule adapter.

- [ ] **Step 3: Implement the OSCAR adapter**

Add `build_subject_discovery_url()`, `parse_subjects()`, and one bounded `discover_subjects()` HTTP call. Return auth, rate-limit, and fetch failures without following redirects. Reuse `AUTH_HOSTS`, `ProbeBudget`, `ProbeSession`, and the existing user agent.

- [ ] **Step 4: Write failing post-probe collector tests**

Prove `collect_subject()` makes exactly one subject request, returns `retry_after_seconds` for 429, and reuses the same parse/validate/publish path as `sync_subject()`. Existing single-subject behavior must remain two requests: one probe plus one collection.

- [ ] **Step 5: Run sync tests and verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_schedule_sync.py
```

Expected: failures because `collect_subject()` and retry metadata are absent.

- [ ] **Step 6: Extract the minimum shared collection path**

Keep `sync_subject()` as the public probe-first function. Add `collect_subject()` for a caller that has already completed a provider-level probe, and route both through one private response-to-publication function. Do not duplicate normalization or repository logic.

- [ ] **Step 7: Run focused tests and verify GREEN**

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_oscar_adapter.py tests/test_schedule_sync.py
```

Expected: all focused tests pass.

- [ ] **Step 8: Commit**

```bash
git add ingestion/schedule/oscar.py ingestion/schedule/sync.py \
  tests/test_oscar_adapter.py tests/test_schedule_sync.py \
  tests/fixtures/oscar_subjects_sample.html
git -c user.name=tkim602 -c user.email=tkim602@gatech.edu \
  commit -m "feat: discover and collect OSCAR subjects"
```

### Task 4: Wire the term CLI without broad live ingestion

**Files:**
- Create: `ingestion/schedule/sync_term.py`
- Modify: `Makefile`
- Create: `tests/test_schedule_term_cli.py`
- Modify: `docs/ingestion.md`

- [ ] **Step 1: Write failing CLI tests**

Test fresh-run argument validation, fixed-manifest resume by `--run-id`, explicit bounded `--subjects CS`, failed-unit retry selection, compact JSON output, and nonzero exit codes for `PAUSED`, `PARTIAL`, and `FAILED`.

- [ ] **Step 2: Run CLI tests and verify RED**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_schedule_term_cli.py
```

Expected: import failure for `ingestion.schedule.sync_term`.

- [ ] **Step 3: Implement the thin OSCAR term command**

Fresh runs create a run ID, perform one provider probe, discover subjects once, optionally restrict the planned manifest to explicitly requested subjects, then call `run_batch()` with a closure around `collect_subject()`.

Resume loads term and policy from the stored run, resets crash-left units, optionally resets explicitly selected failed units, probes provider readiness again, and runs only stored remaining units. It never calls subject discovery.

Add:

```make
sync-oscar-all:
	$(PYTHON) -m ingestion.schedule.sync_term --term "$(term)" \
	  --probe-subject "$(or $(probe_subject),CS)" \
	  --probe-course "$(or $(course),7650)"
```

- [ ] **Step 4: Run CLI tests and verify GREEN**

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_schedule_term_cli.py
```

Expected: all CLI tests pass.

- [ ] **Step 5: Commit**

```bash
git add ingestion/schedule/sync_term.py Makefile \
  tests/test_schedule_term_cli.py docs/ingestion.md
git -c user.name=tkim602 -c user.email=tkim602@gatech.edu \
  commit -m "feat: sync an OSCAR term resumably"
```

### Task 5: Migration and automated verification

**Files:**
- Modify only if verification exposes an implementation defect.

- [ ] **Step 1: Apply migrations**

```bash
alembic upgrade head
alembic current
```

Expected: database reaches revision `004 (head)`.

- [ ] **Step 2: Run focused PostgreSQL integration tests**

```bash
RUN_DB_TESTS=1 PYTHONPATH=$PWD python3 -m pytest -q tests/integration
```

Expected: all integration tests pass.

- [ ] **Step 3: Run the full automated suite**

```bash
PYTHONPATH=$PWD python3 -m pytest -q
```

Expected: zero failures.

- [ ] **Step 4: Run static verification**

```bash
ruff format --check ingestion/orchestration.py ingestion/schedule db/models.py \
  db/migrations/versions/004_ingestion_runs.py tests/test_orchestration.py \
  tests/test_oscar_adapter.py tests/test_schedule_term_cli.py
ruff check ingestion/orchestration.py ingestion/schedule db/models.py \
  db/migrations/versions/004_ingestion_runs.py tests/test_orchestration.py \
  tests/test_oscar_adapter.py tests/test_schedule_term_cli.py
mypy ingestion/orchestration.py ingestion/schedule db/models.py
git diff --check
```

Expected: all commands exit zero.

### Task 6: Perform one bounded live OSCAR verification

**Files:**
- Create: `docs/superpowers/reports/2026-08-20-oscar-horizontal-expansion.md`

- [ ] **Step 1: Verify the command is explicitly bounded**

Use one subject only:

```bash
python3 -m ingestion.schedule.sync_term \
  --term 202608 \
  --probe-subject CS \
  --probe-course 7650 \
  --subjects CS \
  --concurrency 1
```

Expected network bound: one OSCAR probe, one subject-discovery request, and one CS subject request. Do not run the unbounded `make sync-oscar-all term=202608` command.

- [ ] **Step 2: Verify the stored summary**

Expected summary shape:

```json
{
  "run_id": "<uuid>",
  "provider": "public-oscar",
  "scope": {"term": "202608", "selection": "explicit"},
  "status": "COMPLETED",
  "planned": 1,
  "succeeded": 1,
  "failed": 0,
  "remaining": 0,
  "complete": true
}
```

Verify the run has one immutable `CS` unit and the new CS version is published. Record actual request counts and row totals without printing secrets.

- [ ] **Step 3: Record operator commands**

Document fresh full-run, resume, and retry-failed commands. Clearly label the full Fall 2026 all-subject command as user-operated and not executed by Codex.

- [ ] **Step 4: Final verification and commit**

Re-run the full suite and static checks, then commit the report and any verified fixes using the `tkim602` identity.

### Task 7: Publish from the user-named data branch

- [ ] **Step 1: Verify branch and identity**

```bash
git branch --show-current
git show -s --format='%an <%ae>%n%cn <%ce>' HEAD
```

Expected branch: `data/oscar-horizontal-expansion`; author and committer: `tkim602 <tkim602@gatech.edu>`.

- [ ] **Step 2: Push the data branch and create the PR**

```bash
git push -u origin data/oscar-horizontal-expansion
gh pr create --base main --head data/oscar-horizontal-expansion \
  --title "Add resumable OSCAR term ingestion" \
  --body "$(printf '%s\n' \
    '## Summary' \
    '- add immutable PostgreSQL run manifests and resumable bounded scheduling' \
    '- add OSCAR subject discovery and rate-limit-aware term synchronization' \
    '- preserve validated subject-level publication and document the operator flow' \
    '' \
    '## Verification' \
    '- full automated test suite' \
    '- PostgreSQL migration and integration suite' \
    '- one-subject bounded live OSCAR verification')"
```

Do not force-push or delete the earlier remote branch. The new PR supersedes the old draft PR; close the old PR only after the new PR exists.
