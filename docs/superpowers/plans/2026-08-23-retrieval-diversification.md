# Retrieval Diversification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test whether fixing the overly broad calendar filter and limiting same-document chunks in the candidate pool raises retrieval dev-100 Hit@5 by at least five percentage points without lowering MRR.

**Architecture:** Keep query generation, embeddings, vector depth, FTS formulation, RRF, reranking, and final top-k unchanged. Remove the implicit generic deadline-to-calendar restriction, then apply an optional per-document cap to policy-document channel candidates before the existing RRF/reranker; use a bounded deeper FTS fetch only to backfill slots discarded by that cap.

**Tech Stack:** Python 3.12, pytest, SQLAlchemy/PostgreSQL, existing hybrid retrieval and cross-encoder.

---

### Task 1: Reproduce the baseline

**Files:**
- Preserve: `eval/quality/reports_retrieval_100/`
- Create at runtime only: `eval/quality/reports_retrieval_100_repro/`

- [ ] **Step 1: Verify the compatible reranker runtime**

Run:

```bash
python3 - <<'PY'
from sentence_transformers import CrossEncoder

model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
assert len(model.predict([["deadline", "application deadline"]])) == 1
PY
```

Expected: exit `0`; no `Numpy is not available` fallback.

- [ ] **Step 2: Run the unchanged retrieval dev-100**

Run:

```bash
PYTHONPATH=$PWD python3 -m eval.quality.runner \
  --manifest eval/quality/manifests/dev_100.json \
  --report-dir eval/quality/reports_retrieval_100_repro
```

Expected: production Hit@5 is approximately the frozen `42%` baseline. If it is not reproducible, stop before editing production code and diagnose environment/evaluation drift.

- [ ] **Step 3: Record the exact baseline metrics and case IDs**

Compare `latest_summary.json` and production rows in `latest_cases.jsonl` against the frozen report in `/Users/tkim01/Desktop/personal_project/BuzzBot_quality_eval/eval/quality/reports_retrieval_100/`.

### Task 2: Fix the implicit calendar restriction

**Files:**
- Modify: `app/retrieval/documents.py`
- Modify: `tests/test_document_retrieval.py`

- [ ] **Step 1: Write the failing routing regression test**

Add a parameterized test showing generic policy questions containing `when` or `deadline` use all official sources when no explicit `source_types` are supplied. Keep the existing test that explicit source types win.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_document_retrieval.py
```

Expected: the new test fails because `source_filter` is restricted to `gt-academic-calendar`.

- [ ] **Step 3: Implement the smallest routing fix**

Delete the generic `DEADLINE_RE` fallback from `search_policy_docs`; retain explicit `source_types`, otherwise use `OFFICIAL_SOURCE_NAMES`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same pytest command. Expected: pass.

- [ ] **Step 5: Commit the isolated deterministic fix**

```bash
git add app/retrieval/documents.py tests/test_document_retrieval.py
git commit -m "fix: avoid implicit calendar-only policy routing"
```

### Task 3: Add policy candidate document diversification

**Files:**
- Modify: `app/rag/retrieval.py`
- Modify: `app/retrieval/documents.py`
- Modify: `tests/test_retrieval.py`
- Modify: `tests/test_document_retrieval.py`

- [ ] **Step 1: Write failing tests for the candidate cap**

Add one unit test proving an ordered list of chunks is capped per canonical URL while preserving order, and one policy retrieval test proving `search_policy_docs` requests `max_chunks_per_url=1`. URLs without a usable value remain independently eligible rather than being collapsed together.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_retrieval.py tests/test_document_retrieval.py
```

Expected: failures because the cap helper/argument does not exist.

- [ ] **Step 3: Implement the minimal optional cap**

Add an optional `max_chunks_per_url` argument to `hybrid_retrieve`. When set, fetch a bounded deeper FTS pool (`fusion_top_k * 3`), cap vector and FTS candidates by normalized URL before the existing RRF, and retain at most `fusion_top_k` candidates per channel. Keep the default `None`, so schedule/course/other callers are unchanged. Pass `1` only from `search_policy_docs`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the same focused pytest command. Expected: pass.

- [ ] **Step 5: Commit the isolated diversification fix**

```bash
git add app/rag/retrieval.py app/retrieval/documents.py \
  tests/test_retrieval.py tests/test_document_retrieval.py
git commit -m "fix: diversify policy retrieval candidates"
```

### Task 4: Run retrieval-only dev-100 and apply the gate

**Files:**
- Create at runtime only: `eval/quality/reports_retrieval_100_diverse/`

- [ ] **Step 1: Run retrieval dev-100 once with cap 1**

```bash
PYTHONPATH=$PWD python3 -m eval.quality.runner \
  --manifest eval/quality/manifests/dev_100.json \
  --report-dir eval/quality/reports_retrieval_100_diverse
```

- [ ] **Step 2: Compare outcomes**

Record Hit@5, MRR@5, unique URLs in top-k, wins, regressions, unchanged hits, unchanged misses, and the four calendar-filter cases.

- [ ] **Step 3: Apply the acceptance gate**

Accept only if Hit@5 is at least baseline `+0.05`, MRR@5 is not lower, and wins exceed regressions. If cap 1 clearly loses a useful second chunk from the same document, test cap 2 exactly once; otherwise do not run another tuning experiment. If no candidate passes, revert Task 3 and stop without chat evaluation.

### Task 5: Run chat only after retrieval acceptance

**Files:**
- Create at runtime only: `eval/quality/reports_chat_100_diverse/`

- [ ] **Step 1: Check readiness and remaining budget**

Confirm `/ready` is healthy and project usage remains below the `$3` hard cap.

- [ ] **Step 2: Run chat dev-100 exactly once**

Only after every Task 4 gate passes:

```bash
PYTHONPATH=$PWD python3 -m eval.quality.chat_runner \
  --manifest eval/quality/manifests/dev_100.json \
  --report-dir eval/quality/reports_chat_100_diverse
```

Do not retry or run change-200/full chat in this iteration.

### Task 6: Final verification

**Files:**
- No additional production files.

- [ ] **Step 1: Run full automated verification**

```bash
PYTHONPATH=$PWD python3 -m pytest -q
RUN_DB_TESTS=1 PYTHONPATH=$PWD python3 -m pytest -q tests/integration
ruff check .
ruff format --check .
```

Expected: all tests and lint pass.

- [ ] **Step 2: Verify scope and authorship**

Confirm only the plan, two retrieval modules, and focused tests are tracked changes; runtime reports remain untracked. Confirm commits use `tkim602 <tkim602@gatech.edu>`.
