# BuzzBot LangSmith Evaluation Implementation Plan

> **For agentic workers:** Use `executing-plans` task-by-task. Keep product behavior unchanged.

**Goal:** Add optional LangSmith tracing and a reproducible Course Details 20 evaluation before tuning BuzzBot.

**Architecture:** Keep the existing LangGraph and direct OpenAI SDK. LangGraph supplies node traces; LangSmith wraps the cached OpenAI client for nested model spans. A separate `eval/langsmith` package imports the frozen external cases, invokes the production graph, scores deterministic stages, reuses the existing strict answer judge, and uploads/report results.

**Tech Stack:** Python, LangGraph, LangSmith, OpenAI SDK, SQLAlchemy, pytest.

---

### Task 1: Optional tracing

- [ ] Add `langsmith` as a direct dependency and document standard environment variables.
- [ ] Test tracing-disabled and tracing-enabled OpenAI client construction.
- [ ] Wrap the existing cached async OpenAI client only when tracing is enabled.
- [ ] Expose bounded graph diagnostics without changing routing or answers.

### Task 2: Frozen dataset import

- [ ] Test that only the 20 `course_details` cases are loaded from the frozen 500-case file.
- [ ] Preserve stable case IDs, expected route, answer, URLs, sources, and metadata.
- [ ] Create-or-verify the versioned LangSmith dataset without mutating source files.

### Task 3: Stage evaluators

- [ ] Test route, slots, retrieval ranks, evidence validation, citations, abstention, and failure-stage precedence.
- [ ] Reuse `normalize_url` and the existing strict semantic judge.
- [ ] Keep correctness and evidence support as separate scores.

### Task 4: Bounded experiment and report

- [ ] Run the production LangGraph target sequentially for Course Details 20.
- [ ] Upload traces/evaluator outputs to the explicit LangSmith project.
- [ ] Write `docs/evals/course_details_langsmith_baseline.md` with metrics and per-case stages.
- [ ] Do not tune retrieval, prompts, routing, or graph behavior.

### Task 5: Verification

- [ ] Run focused tests, the full unit suite, DB integration tests, Ruff, and `git diff --check`.
- [ ] Confirm tracing-disabled mode requires no LangSmith key and usage accounting remains unchanged.
- [ ] Record how Policy RAG 300 and English NLU stress can reuse the same runner; do not run them in this iteration.
