# Phase 4 Controlled LangGraph Agentic RAG Plan

**Goal:** Orchestrate BuzzBot's validated SQL and hybrid document retrieval through a bounded,
citation-first LangGraph workflow suitable for a portfolio and production hardening.

**Architecture:** Use LangGraph's low-level `StateGraph` API with explicit nodes and conditional
edges. Deterministic rules understand and route common GT questions. Retrieval is read-only and
authority-pinned. Missing evidence gets one bounded retry; invalid evidence or citations terminates
in a transparent abstention. The graph stores only compact serializable state and accepts injected
services for testing and API integration.

**Primary references:**
- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://docs.langchain.com/oss/python/langgraph/use-graph-api
- https://docs.langchain.com/oss/python/langgraph/persistence

**Tech Stack:** Python 3.11, LangGraph 1.x, SQLAlchemy async sessions, existing retrieval and
answering modules, pytest.

## Constraints

- No open-ended ReAct loop, multi-agent delegation, arbitrary URL fetch, or student-account action.
- Nodes: understand, retrieve, validate evidence, prepare one retry, answer, validate citations,
  abstain.
- Course schedules use structured OSCAR SQL; Catalog, Calendar, and policy use official-document
  hybrid retrieval.
- The query embedding is created once per retrieval attempt and normal retrieval is capped at 5
  evidence items.
- Every factual answer must have a canonical official citation; otherwise abstain.
- LLM and embedding calls remain behind the existing `$3` usage guard.
- Checkpoint persistence remains an optional compile-time dependency and is implemented in Phase 5.

## Task 1: State and Deterministic Understanding

**Files:**
- Modify: `pyproject.toml`
- Create: `app/graph/__init__.py`
- Create: `app/graph/state.py`
- Create: `app/graph/understanding.py`
- Test: `tests/test_graph_understanding.py`

- [ ] Add LangGraph 1.x as the only new workflow dependency.
- [ ] Define compact serializable state/evidence types.
- [ ] Parse course code and explicit GT term code deterministically and map the existing router intent
  to schedule, course detail, calendar, or policy retrieval.
- [ ] Return clarification/abstention metadata when a schedule request lacks course or term.

## Task 2: Bounded Workflow

**Files:**
- Create: `app/graph/workflow.py`
- Test: `tests/test_graph_workflow.py`

- [ ] Write graph tests with injected retrieval/answer services before implementation.
- [ ] Build explicit nodes and edges with a single retrieval retry ceiling.
- [ ] Convert typed SQL/document evidence into compact evidence state with official citations.
- [ ] Format schedule results deterministically; use the existing cheap answerer only for documents.
- [ ] Validate citation URL and quote grounding before returning an answer.
- [ ] Compile without a checkpointer by default but accept an optional checkpointer.

## Verification

```bash
PYTHONPATH=$PWD pytest -q tests/test_graph_understanding.py tests/test_graph_workflow.py
PYTHONPATH=$PWD pytest -q
RUN_DB_TESTS=1 PYTHONPATH=$PWD pytest -q tests/integration
ruff check app/graph tests/test_graph_understanding.py tests/test_graph_workflow.py
mypy --follow-imports=skip app/graph
git diff --check
```
