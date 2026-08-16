# Phase 1 Structured OSCAR Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest one bounded public OSCAR term/subject into versioned PostgreSQL tables, reject incomplete batches, atomically publish valid data, and answer exact schedule queries through SQL.

**Architecture:** The existing probe gates a single subject synchronization. The public OSCAR HTML is snapshotted and parsed deterministically into typed records, validated without an LLM, inserted under a staged `data_version`, and made current in one transaction. Queries join only the latest published version, leaving the previous published version intact until replacement succeeds.

**Tech Stack:** Python 3.11, existing httpx/lxml, SQLAlchemy 2, PostgreSQL 16, Alembic, pytest.

## Global Constraints

- Work only in `codex/phase0-oscar-probe`; do not touch dirty `main`.
- No GT credentials, cookies, auth headers, CSRF tokens, LLMs, embeddings, or OpenAI initialization.
- Probe Fall 2026 CS 7650 before the one allowed live Fall 2026 CS subject synchronization.
- One subject request is a fixed collection unit; do not discover unrelated URLs.
- A 429 or authentication redirect stops that provider run without replacing published data.
- Preserve safe raw response body plus allowlisted metadata only.
- Parse success must be at least 99%; every failure is retained.
- Validate requested units, uniqueness, references, required/TBA fields, non-empty coverage, and freshness before publishing.
- Publishing is one database transaction; old published rows remain queryable until the new version commits.
- Current/future schedule freshness target is 6 hours and hard maximum is 24 hours.
- Use TDD for parsing, validation, publication, and retrieval behavior.

---

### Task 1: Production Schedule Types and Normalization

**Files:**
- Create: `ingestion/schedule/__init__.py`
- Create: `ingestion/schedule/types.py`
- Create: `ingestion/schedule/normalize.py`
- Modify: `ingestion/probes/oscar.py`
- Test: `tests/test_schedule_normalize.py`
- Modify test: `tests/test_oscar_probe.py`

**Interfaces:**
- `NormalizedCourse(subject, course_number, title, credits)`
- `NormalizedSection(term_code, term_name, crn, course_key, section_code, campus, schedule_type, instructors, meetings)`
- `NormalizedMeeting(meeting_type, days, start_time, end_time, building, room, start_date, end_date, is_tba)`
- `normalize_sections(term_code: str, samples: list[OscarSectionSample]) -> tuple[list[NormalizedCourse], list[NormalizedSection], list[ParseFailure]]`
- `parse_schedule_listing(html: str, max_records: int | None) -> tuple[list[OscarSectionSample], list[OscarParseFailure]]`

- [ ] Write failing tests proving timed/TBA parsing, `3:30 pm` conversion, building/room split, date range parsing, instructor deduplication, unlimited parse mode, and malformed section retention.
- [ ] Run focused tests and confirm failures are caused by missing schedule modules/signatures.
- [ ] Implement frozen dataclasses and deterministic parsing using `datetime.strptime`, explicit TBA flags, and literal `(subject, course_number)` keys.
- [ ] Change the OSCAR parser to return successful rows plus failure records and update probe callers/tests to count only successful rows.
- [ ] Run focused tests, existing probe tests, Ruff, and mypy.
- [ ] Commit as `feat: normalize OSCAR schedule records`.

Required assertions include:

```python
assert meeting.start_time == time(15, 30)
assert meeting.end_time == time(16, 45)
assert meeting.building == "Paper Tricentennial"
assert meeting.room == "109"
assert tba.is_tba is True
assert tba.start_time is None
assert failures[0].error_code == "SECTION_HEADER_INVALID"
```

---

### Task 2: Versioned Structured Schema

**Files:**
- Modify: `db/models.py`
- Create: `db/migrations/versions/003_structured_schedule.py`
- Test: `tests/test_schedule_models.py`

**Interfaces:**
- Models: `DataVersion`, `AcademicTerm`, `Course`, `Section`, `Meeting`, `SourceSnapshot`, `IngestionError`.
- All structured rows contain `data_version_id`; `Section` references `AcademicTerm` and `Course`; `Meeting` references `Section`.
- Unique constraints: `(provider, requested_unit, status)` is not required; `(data_version_id, subject, course_number)` and `(data_version_id, term_code, crn)` are required.

- [ ] Write failing metadata tests asserting table names, unique constraints, foreign keys, nullable TBA columns, and relationship targets.
- [ ] Run the tests and verify they fail before models exist.
- [ ] Add the SQLAlchemy models using existing UUID/JSON conventions and cascade only version-owned child rows.
- [ ] Add Alembic revision `003` with indexes for published-version lookup, course key lookup, `(term_code, crn)`, instructors JSON, and meeting day/time filters.
- [ ] Run model tests, import checks, Ruff, and mypy on changed files.
- [ ] Commit as `feat: add versioned academic schedule schema`.

The version state values stored as strings are `STAGED`, `PUBLISHED`, `FAILED`, and `SUPERSEDED`; publication timestamp is nullable until publish.

---

### Task 3: Collection Validation and Freshness

**Files:**
- Create: `ingestion/schedule/validate.py`
- Test: `tests/test_schedule_validate.py`

**Interfaces:**
- `CollectionPlan(term_code, planned_subjects, completed_subjects, failed_units, records_fetched, records_parsed)`
- `ValidationIssue(code, record_id, message)`
- `ValidationReport(valid, parse_success_rate, issues)`
- `validate_collection(plan, courses, sections, failures, fetched_at) -> ValidationReport`
- `freshness_state(fetched_at, now, target_hours=6, max_hours=24, historical=False) -> FreshnessState`

- [ ] Write one failing test per gate: partial subject, <99% parse rate, duplicate CRN, missing course reference, empty result, missing meeting/TBA representation, future timestamp, and freshness transitions.
- [ ] Verify each test fails for the intended missing behavior.
- [ ] Implement one linear validator that accumulates issues; do not create a rule framework.
- [ ] Treat completed historical data as non-expiring only when `historical=True`; always preserve `data_as_of`.
- [ ] Run focused tests, Ruff, and mypy.
- [ ] Commit as `feat: validate schedule collection batches`.

Representative expectations:

```python
assert validate_collection(partial_plan, courses, sections, [], fetched_at).valid is False
assert "COLLECTION_INCOMPLETE" in issue_codes
assert freshness_state(now - timedelta(hours=7), now) is FreshnessState.STALE
assert freshness_state(now - timedelta(hours=25), now) is FreshnessState.EXPIRED
```

---

### Task 4: Atomic Stage and Publish Repository

**Files:**
- Create: `ingestion/schedule/repository.py`
- Test: `tests/test_schedule_repository.py`
- Test integration: `tests/integration/test_schedule_publication.py`

**Interfaces:**
- `publish_collection(session: Session, provider: str, requested_unit: str, snapshot: SafeSnapshot, plan: CollectionPlan, courses, sections, failures, report) -> UUID`
- `latest_published_version(session: Session, provider: str, requested_unit: str) -> DataVersion | None`
- A failed validation inserts a `FAILED` version/errors but does not supersede the previous `PUBLISHED` version.
- A valid publication inserts all rows, marks the previous published version `SUPERSEDED`, and marks the new version `PUBLISHED` in the same transaction.

- [ ] Write failing repository unit tests for invalid-report rejection and row mapping.
- [ ] Write a PostgreSQL integration test proving an exception before commit leaves the old published version unchanged.
- [ ] Implement explicit insert functions; do not use a generic repository abstraction.
- [ ] Start the existing Docker PostgreSQL service, migrate to head, and run the integration test with `RUN_DB_TESTS=1`.
- [ ] Run focused tests and SQLAlchemy/Alembic checks.
- [ ] Commit as `feat: publish validated schedule versions atomically`.

---

### Task 5: Bounded Subject Sync CLI

**Files:**
- Create: `ingestion/schedule/sync.py`
- Create: `ingestion/schedule/cli.py`
- Test: `tests/test_schedule_sync.py`
- Modify: `docs/probes.md`

**Interfaces:**
- `build_subject_listing_url(term: str, subject: str) -> str`
- `sync_subject(term, subject, probe_course, output_dir, session_factory, transport=None) -> SyncResult`
- CLI: `python3 -m ingestion.schedule.cli --term 202608 --subject CS --probe-course 7650`
- The CLI probes first, performs one subject request only after `READY`, writes a safe snapshot, validates, stages, publishes, and prints compact counts.

- [ ] Write failing tests for probe failure preventing sync, one subject request after READY, 429 preserving published data, and compact result counts.
- [ ] Verify RED, then implement the minimum orchestration using existing `ProbeSession` and the repository.
- [ ] Ensure stdout never includes response bodies or all parsed rows.
- [ ] Run focused tests, Ruff, and mypy.
- [ ] Commit as `feat: sync one validated OSCAR subject`.

---

### Task 6: Read-Only Structured Retrieval

**Files:**
- Create: `app/retrieval/__init__.py`
- Create: `app/retrieval/schedule.py`
- Test: `tests/test_schedule_retrieval.py`
- Test integration: `tests/integration/test_schedule_retrieval.py`

**Interfaces:**
- `CourseQuery(term_code, subject, course_number, campus=None, days=None, starts_after=None, ends_before=None)`
- `lookup_course_offerings(session: AsyncSession, query: CourseQuery) -> list[CourseOffering]`
- `lookup_sections` is an alias only if a distinct consumer requires it; otherwise one function serves both use cases.
- Results include source URL, `data_as_of`, data version, and freshness state.

- [ ] Write failing query-building tests and PostgreSQL integration tests for exact offering, CRN, instructor, timed/TBA meeting, campus, and day/time filters.
- [ ] Implement parameterized SQLAlchemy selects joining only `PUBLISHED` versions.
- [ ] Run integration tests against the one-subject published fixture/version.
- [ ] Run focused tests, full suite, Ruff, mypy, and `git diff --check`.
- [ ] Commit as `feat: query published course offerings`.

---

### Task 7: One Live Representative Publication

**Files:**
- Creates ignored safe snapshots/reports only.

- [ ] Run the existing bounded probe once for `202608 / CS / 7650`.
- [ ] If `READY`, run exactly one `202608 / CS` subject synchronization.
- [ ] Record request count, fetched/parsed/failure counts, validation result, data version, and published row counts without dumping rows.
- [ ] Run one SQL retrieval smoke query for CS 7650 and verify CRNs/times/instructors are returned with `data_as_of`.
- [ ] If the provider is unavailable, retain fixture-backed verification and record the limitation without retry expansion.
