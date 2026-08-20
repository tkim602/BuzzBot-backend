# Phase 3 Typed Retrieval Tools Plan

**Goal:** Expose the validated schedule SQL and official-document hybrid retrieval as a small,
read-only tool surface that a controlled LangGraph workflow can call.

**Architecture:** Keep the existing retrieval implementations as the only data access paths.
Add typed query objects and two narrow document wrappers for course-catalog details and registration
calendar evidence. Re-export the existing schedule lookup directly; do not add a second ranking layer,
agent framework, or student-account action.

**Tech Stack:** Python 3.11, SQLAlchemy async sessions, existing pgvector/FTS/RRF retrieval, pytest.

## Constraints

- All tools are read-only and return typed evidence with canonical citations.
- Course offerings come only from the latest published structured OSCAR version.
- Course details come only from the official Catalog source.
- Exact registration dates come only from the official Academic Calendar source.
- Query embeddings are supplied by the caller so a graph can budget and cache them once.
- `lookup_course_offerings` remains the section lookup; no redundant alias is added.

## Task 1: Narrow Document Tools

**Files:**
- Create: `app/retrieval/tools.py`
- Modify: `app/retrieval/__init__.py`
- Test: `tests/test_retrieval_tools.py`

- [ ] Write failing validation and delegation tests.
- [ ] Add `CourseDetailsQuery`, `RegistrationCalendarQuery`, `lookup_course_details`, and
  `lookup_registration_calendar` as thin wrappers over `search_policy_docs`.
- [ ] Prove source-type filters cannot be overridden by free-form user text.
- [ ] Run focused/full tests, Ruff, mypy, and commit.

## Verification

```bash
PYTHONPATH=$PWD pytest -q tests/test_retrieval_tools.py tests/test_document_retrieval.py \
  tests/test_schedule_retrieval.py
PYTHONPATH=$PWD pytest -q
RUN_DB_TESTS=1 PYTHONPATH=$PWD pytest -q tests/integration
ruff check app/retrieval tests/test_retrieval_tools.py
mypy --follow-imports=skip app/retrieval/tools.py
git diff --check
```
