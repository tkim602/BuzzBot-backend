# Policy Hierarchical Retrieval Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether decisive Policy evidence is reliably retrievable when chunk ranking is restricted to the correct official document.

**Architecture:** Add one evaluation-only runner that compares the existing production Policy path with an oracle-document path. The oracle path loads chunks for the committed gold-evidence URL and reuses existing vector, lexical, RRF, and cross-encoder signals without changing production retrieval.

**Tech Stack:** Python 3.11, asyncio, SQLAlchemy, pgvector, pytest, existing BuzzBot retrieval/evaluation helpers.

---

### Task 1: Lock the evaluation contract

**Files:**
- Create: `tests/test_policy_oracle_retrieval.py`
- Create: `eval/quality/policy_oracle_retrieval.py`

- [x] Write failing tests proving the oracle candidate function keeps only the gold URL and returns at most five ranked chunks.
- [x] Write failing tests for Evidence Hit@1/3/5, MRR@5, mean/p95 latency, and PR11 category grouping.
- [x] Run `python -m pytest -q tests/test_policy_oracle_retrieval.py` and confirm failure because the evaluator does not exist.

### Task 2: Implement the evaluator-only oracle path

**Files:**
- Create: `eval/quality/policy_oracle_retrieval.py`
- Modify: `Makefile`
- Modify: `eval/quality/README.md`

- [x] Load the fixed manifest, committed gold evidence, and PR11 taxonomy with strict denominator validation.
- [x] Batch query embeddings once and run the existing production Policy retriever for the global baseline.
- [x] Load each gold document's chunks, rank them using existing vector, lexical, RRF, and cross-encoder helpers, and retain the existing final top-five budget.
- [x] Write summary JSON, per-case JSONL, and Markdown outputs.
- [x] Add `make quality-policy-oracle` and document that it makes embedding/retrieval calls but no answer/judge calls.
- [x] Run the focused tests and confirm they pass.

### Task 3: Run the fixed experiment once

**Files:**
- Create: `eval/quality/policy_oracle_retrieval_pr12.json`
- Create: `eval/quality/policy_oracle_retrieval_pr12_cases.jsonl`
- Create: `docs/evals/policy_oracle_retrieval_pr12.md`

- [x] Run the evaluator against `dev_100.json`, `gold_evidence/dev_100.json`, and the current `buzzbot` database.
- [x] Record global and oracle Evidence Hit@1/3/5, oracle MRR@5, mean/p95 latency, and unresolved PR11 category results.
- [x] Apply the predeclared 90% oracle Hit@5 architectural decision rule without changing retrieval code.

### Task 4: Verify regressions

**Files:**
- Modify only generated PR12 result/report files if values need normalization.

- [x] Run `python -m pytest -q` and expect the full unit suite to pass.
- [x] Run `ruff check .` and `ruff format --check .` and expect both to pass.
- [x] Run the existing PostgreSQL integration, Schedule SQL/NLU/renderer, Course Details, and Calendar gates.
- [x] Confirm the PR10 frozen fixture hashes are unchanged and no live Policy answer evaluation was executed.
