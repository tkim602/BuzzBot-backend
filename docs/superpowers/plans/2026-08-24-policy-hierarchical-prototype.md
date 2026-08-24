# Policy Hierarchical Retrieval Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare non-oracle hierarchical Policy retrieval at document counts 1, 2, 3, and 5 on the fixed dev-100 benchmark.

**Architecture:** Reuse production document ranking for Stage 1 and the PR12 oracle evaluator's vector/lexical/RRF chunk ranking for Stage 2. Merge bounded candidates with the existing cross-encoder and keep the current final top-five evidence budget.

**Tech Stack:** Python 3.11, asyncio, SQLAlchemy, pgvector, existing BuzzBot retrieval helpers, pytest, Ruff.

---

### Task 1: Lock the bounded comparison contract

**Files:**
- Create: `tests/test_policy_hierarchical_retrieval.py`
- Create: `eval/quality/policy_hierarchical_retrieval.py`
- Modify: `eval/quality/policy_oracle_retrieval.py`

- [x] Add a failing test that only `N = (1, 2, 3, 5)` is accepted and selected document prefixes contain no gold-aware behavior.
- [x] Add a failing test that cross-document merge returns at most five chunks from the selected URLs.
- [x] Add a failing test that candidate selection chooses the smallest passing `N` at the best Hit@5 and returns no candidate below 85%.
- [x] Run `python -m pytest -q tests/test_policy_hierarchical_retrieval.py` and verify RED because the module does not exist.

### Task 2: Implement the evaluation-only hierarchy

**Files:**
- Create: `eval/quality/policy_hierarchical_retrieval.py`
- Modify: `eval/quality/policy_oracle_retrieval.py`
- Modify: `Makefile`
- Modify: `eval/quality/README.md`

- [x] Add an optional evaluator-only switch that lets PR13 reuse PR12 within-document ranking before the final cross-document rerank.
- [x] Load the fixed manifest/evidence, batch embeddings once, and obtain five Stage 1 documents with existing routed parent retrieval.
- [x] For each `N`, retrieve chunks only from the selected URLs, keep at most 15 candidates per document, rerank the merged pool once, and return five.
- [x] Compute document recall, evidence metrics, mean/p95 latency, wins/regressions versus the fixed production result, and the 85%/90% gates.
- [x] Write JSON, JSONL, and Markdown artifacts and add `make quality-policy-hierarchical`.
- [x] Run focused tests and verify GREEN.

### Task 3: Execute one fixed dev-100 comparison

**Files:**
- Create: `eval/quality/policy_hierarchical_retrieval_pr13.json`
- Create: `eval/quality/policy_hierarchical_retrieval_pr13_cases.jsonl`
- Create: `docs/evals/policy_hierarchical_retrieval_pr13.md`

- [x] Run exactly `N = 1, 2, 3, 5` against `buzzbot` once.
- [x] Record all required metrics and choose a candidate only through the predeclared gate.
- [x] Keep the eight PR12 oracle misses unchanged and skip paid semantic answer evaluation.

### Task 4: Verify regressions

**Files:**
- Modify only PR13 result/report artifacts if verification metadata is added.

- [x] Run the full unit suite and PostgreSQL integration suite.
- [x] Run Ruff check, format check, and `git diff --check`.
- [x] Run Schedule SQL/NLU/renderer, Course Details, and Calendar frozen gates.
- [x] Confirm PR10 fixture hashes and production retrieval files are unchanged.
