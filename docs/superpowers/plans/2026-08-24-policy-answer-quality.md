# Policy Answer Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze retrieval, diagnose the 21 answer-layer failures, improve Policy synthesis/citation quality, and add a measurable Calendar chat gate.

**Architecture:** Reuse the accepted retrieval report as an immutable top-five evidence snapshot. Run the existing answerer and validators directly over that snapshot, then publish separated deterministic and semantic metrics through the existing LangSmith evaluation pattern.

**Tech Stack:** Python 3.12, pytest, LangGraph answerer/grounding functions, LangSmith Python SDK, JSON fixtures.

---

### Task 1: Freeze Policy evidence and taxonomy

**Files:**
- Create: `eval/frozen/policy_answer_dev_100_v1/snapshot.json`
- Create: `eval/frozen/policy_answer_dev_100_v1/taxonomy.json`
- Create: `eval/frozen/policy_answer_dev_100_v1/README.md`
- Create: `eval/langsmith/run_policy_answer.py`
- Test: `tests/test_policy_answer_eval.py`

- [ ] Write tests that require exactly 100 unique cases, at most five retrieved
  chunks per case, matching manifest IDs, fixed provenance hashes, and exactly
  21 unique taxonomy rows using only the eight approved labels.
- [ ] Run `LANGSMITH_TRACING=false pytest -q tests/test_policy_answer_eval.py`
  and confirm it fails because the loader and artifacts do not exist.
- [ ] Add the minimal dataclasses/loaders to `run_policy_answer.py` and generate
  the two committed JSON artifacts from the accepted reports without querying
  the DB or rerunning retrieval.
- [ ] Rerun the focused test and commit the snapshot/taxonomy slice.

### Task 2: Add frozen-evidence answer and metric evaluation

**Files:**
- Modify: `eval/langsmith/run_policy_answer.py`
- Test: `tests/test_policy_answer_eval.py`

- [ ] Add failing tests proving the runner passes the stored evidence directly
  to `generate_answer`, preserves the raw answer, applies `check_grounding`,
  `check_claim_support`, and `check_binary_polarity`, and fails closed when any
  required validator result is false.
- [ ] Add failing tests for distinct metrics: `answer_correct`,
  `answer_supported`, `citation_present`, `citation_entails_claim`,
  `citation_source_correct`, `abstention_correct`, and
  `unsupported_confident`.
- [ ] Implement only the answer-only target, deterministic metrics, one
  structured semantic evaluator, usage accounting, sequential LangSmith run,
  and Markdown/JSON report writer.
- [ ] Run the focused tests and commit.

### Task 3: Improve the dominant answer-synthesis failure

**Files:**
- Modify only the prompt or existing answer/citation helper identified by the
  frozen baseline diagnosis.
- Test the same existing module plus `tests/test_policy_answer_eval.py`.

- [ ] Execute the frozen 21-case diagnostic baseline and record raw answer,
  selected evidence, citation output, validator result, and taxonomy.
- [ ] State one dominant causal hypothesis from those rows.
- [ ] Write a failing regression test for that single behavior.
- [ ] Implement the smallest shared fix; do not touch retrieval or validator
  thresholds.
- [ ] Run the focused suite. Reject the change if it does not improve the
  targeted bucket or causes any focused regression.

### Task 4: Run the bounded Policy and Calendar evaluations

**Files:**
- Modify: `eval/langsmith/run_policy_answer.py`
- Create: `eval/langsmith/run_calendar_chat.py`
- Test: `tests/test_calendar_chat_eval.py`
- Create: `docs/evals/policy_answer_pr10.md`
- Create: `docs/evals/calendar_chat_baseline.md`

- [ ] Run one 100-case frozen-evidence Policy LangSmith candidate only after
  Task 3 passes offline.
- [ ] Require correctness >= 70%, support >= 75%, citation grounding >= 90%,
  and unsupported confident = 0; otherwise keep the report but reject the
  behavior change.
- [ ] Add tests for a 20-case Calendar answer target that verifies date/event
  correctness, exact citation grounding/source, abstention, and output contract.
- [ ] Run one Calendar chat experiment against the existing frozen 20 events
  and report the result without changing Calendar retrieval.

### Task 5: Broad verification and PR10

**Files:**
- Create: `docs/evals/buzzbot_policy_answer_final.md`

- [ ] Run `LANGSMITH_TRACING=false PYTHONPATH=$PWD pytest -q`.
- [ ] Run `LANGSMITH_TRACING=false RUN_DB_TESTS=1 PYTHONPATH=$PWD pytest -q tests/integration`.
- [ ] Rerun the frozen SQL 150, Schedule/NLU, Course Details 120, and Calendar
  20 gates.
- [ ] Run `ruff check .`, `ruff format --check .`, and `git diff --check`.
- [ ] Record exact results, costs, accepted/rejected gates, and remaining
  limitations; commit as `tkim602`, push `data/policy-answer-quality`, and open
  PR10 stacked on PR9 until PR9 merges.

