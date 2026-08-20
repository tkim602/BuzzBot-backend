# Reusable Ingestion Orchestration Design

**Date:** 2026-08-20

**Status:** Approved design

**Goal:** Expand BuzzBot from one OSCAR subject to broader Georgia Tech data without coupling source-specific collection rules to retry, resume, concurrency, and run-tracking behavior.

## 1. Decision

Use a small data-agnostic orchestration kernel above domain-owned ingestion pipelines.

The kernel controls execution only:

- immutable run planning
- bounded concurrency
- retry and global pause/stop decisions
- checkpoint and resume
- per-unit result recording
- aggregate coverage reporting

Each domain continues to own its data semantics:

- schedule ingestion owns OSCAR discovery, parsing, normalization, validation, and subject-level publication
- document ingestion owns HTML/XHR/PDF/API extraction, chunking, embedding, deduplication, and document storage

Do not create one universal fetch/parse/store provider interface. Schedule rows and normalized documents have different validation and publication contracts. Reuse begins at their common execution boundary, not inside their data pipelines.

## 2. Boundaries

```text
                    Orchestration kernel
             planning / retry / resume / status
                              |
              +---------------+---------------+
              |                               |
       Schedule domain                 Document domain
              |                               |
       public OSCAR adapter       HTML / calendar XHR / future adapters
              |                               |
    term:subject publication        normalized document pipeline
              |                               |
              +---------------+---------------+
                              |
                          BuzzBot DB
```

Initial implementation wires the kernel to OSCAR term expansion only. Existing document synchronization remains unchanged until it has a real batch requirement. The kernel initially accepts discovery and unit-run callables; a shared adapter protocol is introduced only when a second batch domain uses it.

## 3. OSCAR Run Lifecycle

```text
create run
  -> bounded provider probe
  -> discover subjects once
  -> persist immutable planned units
  -> schedule pending subjects with bounded concurrency
  -> fetch -> parse -> normalize -> validate -> publish each subject
  -> record each result
  -> derive run coverage and terminal status
```

The existing single-subject pipeline and atomic `term:subject` publication remain the source of truth. The batch runner does not duplicate parsing, validation, or SQL writes.

The batch path performs one provider-level probe before discovery and unit scheduling. The single-subject command retains its current probe-first behavior. Shared internal collection code may be extracted so the batch path does not issue a redundant probe for every subject.

## 4. Immutable Run Manifest

Every fresh run receives a unique `run_id`. Its discovered subject list is persisted once and never rediscovered during resume.

Required run state:

- `run_id`
- provider and scope, such as `public-oscar` and term `202608`
- start and completion timestamps
- status and stop reason
- concurrency and retry policy used by that run
- immutable planned units in deterministic order
- per-unit status, attempts, result summary, and published version identifier

Resume is defined as:

```text
original planned units - completed units = remaining units
```

Subjects added by Georgia Tech after a run starts belong to the next fresh run. A resume never changes the meaning or denominator of the original run.

Run state is stored in PostgreSQL because the application already requires it and operational state should survive process restarts and different machines. Use one run table and one run-unit table rather than storing an unqueryable JSON checkpoint file. No Redis, Celery, or message broker is introduced.

## 5. Status Model

Run statuses are deliberately small:

- `PLANNED`: manifest persisted; no unit scheduled
- `RUNNING`: units may be scheduled
- `PAUSED`: resumable global condition such as persistent rate limiting
- `COMPLETED`: every planned unit succeeded
- `PARTIAL`: all schedulable work finished, but one or more units failed validation or collection
- `FAILED`: non-resumable run-level failure such as authentication or invalid discovery

Unit statuses:

- `PENDING`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`

Failure codes and retryability remain separate from status. This avoids multiplying states for every provider error.

## 6. Retry and Stop Policy

Authentication failures are global hard stops:

- HTTP 401 or 403
- redirect to a recognized Georgia Tech authentication host
- an authentication-required probe result

On authentication failure, stop scheduling new units immediately, allow no retry, record the reason, and mark the run `FAILED`. Already-running requests are allowed to finish safely, but their completion does not restart scheduling.

Rate limiting is resumable:

```text
429
  -> stop scheduling new units
  -> honor a valid Retry-After value
  -> otherwise use bounded exponential backoff with jitter
  -> retry the affected unit
  -> resume scheduling after a successful retry
  -> mark the run PAUSED / RATE_LIMITED after the retry budget is exhausted
```

Initial defaults are conservative and configurable at the command boundary: concurrency `2`, at most `2` retries after the first 429, and a capped backoff. Retry waits must be interruptible and must never hold a database transaction or worker slot containing unpublished state.

Transient connection errors and 5xx responses use the same bounded retry mechanism but do not globally pause unrelated scheduling unless the provider policy classifies them as provider-wide. Parse and validation failures are deterministic and are not retried automatically.

## 7. Concurrency and Recovery

One process owns a run in the initial implementation. The runner schedules at most the configured number of units and persists every state transition before scheduling more work.

When resuming after a process crash, units left as `RUNNING` are returned to `PENDING` because subject synchronization is safe to repeat and publication is versioned atomically. Successful units are never rerun by normal resume. An explicit retry option may reset selected failed units to `PENDING` within the same manifest.

Distributed worker leasing, heartbeat expiry, and cross-host work stealing are excluded. Add them only if ingestion is actually moved to multiple worker processes.

## 8. Publication and Completeness

OSCAR publication remains independent per `term:subject`:

```text
202608:AE
202608:CS
202608:ECE
```

A valid new subject version supersedes only the previous version of the same subject. Failed, empty, partial, stale, or schema-incompatible data never supersedes a trusted published version.

Term-wide completeness is derived from the immutable run manifest, not inferred from whichever subject rows happen to exist:

```json
{
  "run_id": "<uuid>",
  "term": "202608",
  "planned": 45,
  "succeeded": 44,
  "failed": 1,
  "remaining": 0,
  "complete": false
}
```

The readiness layer may report this coverage, but partial coverage does not make already-published subjects unavailable. It must not claim that a whole term is complete unless the corresponding run is `COMPLETED`.

## 9. Document Adapters

Document adapters stop at a normalized document boundary:

```text
source response -> normalized document
                    -> chunk
                    -> embed changed chunks
                    -> deduplicate and store
```

Planned adapter families are HTML, academic-calendar XHR, PDF, and official JSON API. They do not implement embedding or persistence independently.

The current academic calendar remains an XHR-backed normalized document. A second structured calendar table is not added until a real SQL requirement, such as exact date-range filtering, demonstrates the need for dual storage.

## 10. Commands

Fresh OSCAR run:

```bash
make sync-oscar-all term=202608
```

Resume the same immutable manifest:

```bash
python -m ingestion.schedule.sync_term --run-id <uuid> --resume
```

Retry selected failed units:

```bash
python -m ingestion.schedule.sync_term \
  --run-id <uuid> \
  --retry-failed ARCH,ECE
```

The full live term run remains a user-operated command. Automated verification uses saved fixtures, mocked HTTP responses, and bounded PostgreSQL integration tests; it does not perform a broad OSCAR crawl.

## 11. Required Verification

The implementation is complete only when tests demonstrate:

- discovery is performed once and the planned unit snapshot is immutable
- resume runs only the original incomplete units
- unique run IDs keep separate executions isolated
- bounded concurrency is respected
- one 429 pauses new scheduling, honors retry timing, and can recover
- repeated 429 responses produce a resumable `PAUSED` run
- authentication produces an immediate global `FAILED` run
- a deterministic unit failure does not stop unrelated units
- a crash-left `RUNNING` unit can safely resume
- failed collection never supersedes a published subject version
- coverage counts reconcile exactly with the manifest

## 12. Explicitly Deferred

- a universal provider base class or factory
- Redis, Celery, Kafka, n8n, or a hosted workflow engine
- multiple distributed workers and leases
- term-wide giant publication transactions
- academic-calendar dual storage
- automatic crawling of unregistered Georgia Tech sites

These are added only after a measured requirement exceeds the single-process, PostgreSQL-backed orchestration model.
