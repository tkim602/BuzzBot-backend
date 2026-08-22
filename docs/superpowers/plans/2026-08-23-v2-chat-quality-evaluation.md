# BuzzBot `/v2/chat` Quality Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. This project uses inline execution; do not dispatch subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fast, fixed 100/200-case retrieval gates and a resumable end-to-end `/v2/chat` evaluation that uses the configured `gpt-4o-mini` without automatically running all 1,000 live questions.

**Architecture:** Keep the existing 1,000-query verified dataset as the immutable master. Tiny versioned manifests select fixed variants for the 100- and 200-case tiers. The current retrieval runner consumes those selections, while one new evaluation module calls the real `/v2/chat` HTTP endpoint sequentially, judges completed answers with the existing configured LLM and usage accounting, appends resumable JSONL results, and renders a compact summary.

**Tech Stack:** Python 3.12, asyncio, httpx, FastAPI `/v2/chat`, existing OpenAI configuration and usage guard, pytest, PostgreSQL/pgvector, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-23-v2-chat-quality-evaluation-design.md`

## Global Constraints

- `POST /v2/chat` is the only production chat contract under test; do not modify or evaluate the legacy frontend or `POST /chat`.
- Do not modify the verified 1,000-query files in `eval/quality/data_verified`.
- Do not modify ingestion, crawlers, OSCAR, chunking, document source scope, retrieval behavior, prompts, or the LangGraph workflow.
- Reuse `settings.openai_model`, `app.rag.answerer._call_llm`, and the existing `$3` usage guard; add no model, judge service, dependency, or framework.
- Live implementation verification is limited to two `/v2/chat` cases. Do not run a live 100-, 200-, or 1,000-case chat evaluation automatically.
- Preserve untracked evaluation reports and `eval/quality/schema.py.bak`.
- All commits must use `tkim602 <tkim602@gatech.edu>`.

## File Map

- Create `eval/quality/manifests/dev_100.json`: fixed one-variant-per-fact selection.
- Create `eval/quality/manifests/change_200.json`: fixed two-variants-per-fact selection.
- Modify `eval/quality/schema.py`: load and strictly validate a tier manifest against the master dataset.
- Modify `eval/quality/runner.py`: optionally use a manifest without changing the full benchmark behavior.
- Create `eval/quality/chat_runner.py`: `/v2/chat` calling, LLM judging, resume, metrics, and reports.
- Modify `tests/test_quality_eval.py`: manifest and retrieval-tier regressions.
- Create `tests/test_chat_quality_eval.py`: mocked chat/judge/resume/budget regressions.
- Modify `eval/quality/README.md`: exact fast/full and live/offline commands.
- Modify `Makefile`: explicit 100/200 retrieval and chat targets; no live 1,000-chat target.

---

### Task 1: Fixed tier manifests and strict selection

**Files:**
- Create: `eval/quality/manifests/dev_100.json`
- Create: `eval/quality/manifests/change_200.json`
- Modify: `eval/quality/schema.py:126-171`
- Test: `tests/test_quality_eval.py`

**Interfaces:**
- Consumes: `load_cases(path: Path) -> list[GoldCase]` and the two files in `eval/quality/data_verified`.
- Produces: `load_manifest_cases(path: Path) -> list[GoldCase]`.

- [ ] **Step 1: Add failing manifest-selection tests**

Add these imports and tests to `tests/test_quality_eval.py`:

```python
from eval.quality.schema import GoldCase, load_cases, load_manifest_cases


def test_dev_manifest_selects_one_fixed_case_per_fact():
    cases = load_manifest_cases(Path("eval/quality/manifests/dev_100.json"))

    assert len(cases) == 100
    assert len({case.variant_group for case in cases}) == 100
    assert {case.id.rsplit("-", 1)[-1] for case in cases} == {"v3"}


def test_change_manifest_selects_two_fixed_cases_per_fact():
    cases = load_manifest_cases(Path("eval/quality/manifests/change_200.json"))

    assert len(cases) == 200
    counts = Counter(case.variant_group for case in cases)
    assert len(counts) == 100
    assert set(counts.values()) == {2}
    assert {case.id.rsplit("-", 1)[-1] for case in cases} == {"v3", "v10"}


def test_manifest_fails_when_a_requested_variant_is_missing(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "cases.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "gold-001-v1",
                        "variant_group": "gold-001",
                        "question": "Question?",
                        "gold_answer": "Answer.",
                        "gold_urls": ["https://example.gatech.edu/rule"],
                        "gold_sources": ["gt-example"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "broken",
                "master_dataset": "dataset",
                "variant_suffixes": ["v2"],
                "expected_fact_count": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest selection is incomplete"):
        load_manifest_cases(manifest)
```

Also add `from collections import Counter` at the top.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_quality_eval.py::test_dev_manifest_selects_one_fixed_case_per_fact \
  tests/test_quality_eval.py::test_change_manifest_selects_two_fixed_cases_per_fact \
  tests/test_quality_eval.py::test_manifest_fails_when_a_requested_variant_is_missing
```

Expected: collection fails because `load_manifest_cases` and the manifest files do not exist.

- [ ] **Step 3: Add the two small versioned manifests**

Create `eval/quality/manifests/dev_100.json`:

```json
{
  "name": "buzzbot_gt_public_dev_100",
  "master_dataset": "../data_verified",
  "variant_suffixes": ["v3"],
  "expected_fact_count": 100
}
```

Create `eval/quality/manifests/change_200.json`:

```json
{
  "name": "buzzbot_gt_public_change_200",
  "master_dataset": "../data_verified",
  "variant_suffixes": ["v3", "v10"],
  "expected_fact_count": 100
}
```

`v3` is the fixed student-scenario variant and `v10` is the fixed student-chat variant. The manifest stores the selection rule rather than duplicating 300 gold records.

- [ ] **Step 4: Implement strict manifest loading**

Add this function below `load_cases` in `eval/quality/schema.py`:

```python
def load_manifest_cases(path: Path) -> list[GoldCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    suffixes = tuple(str(value) for value in payload.get("variant_suffixes", []))
    expected_facts = int(payload.get("expected_fact_count", 0))
    master_value = payload.get("master_dataset")
    if not isinstance(master_value, str) or not suffixes or expected_facts < 1:
        raise ValueError(f"{path}: invalid evaluation manifest")
    if len(set(suffixes)) != len(suffixes):
        raise ValueError(f"{path}: duplicate variant suffix")

    master = (path.parent / master_value).resolve()
    selected = [
        case
        for case in load_cases(master)
        if case.id.rsplit("-", 1)[-1] in suffixes
    ]
    counts = Counter(case.variant_group for case in selected)
    if len(counts) != expected_facts or set(counts.values()) != {len(suffixes)}:
        raise ValueError(f"{path}: manifest selection is incomplete")
    return selected
```

Add `from collections import Counter` to `eval/quality/schema.py`. Do not change `load_cases` or the master JSON files.

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_eval.py
```

Expected: all tests in `tests/test_quality_eval.py` pass, including 100/200/1,000 count invariants.

- [ ] **Step 6: Commit**

```bash
git add eval/quality/manifests eval/quality/schema.py tests/test_quality_eval.py
git commit -m "eval: add fixed quality tiers"
```

---

### Task 2: Reuse the existing retrieval runner for 100/200 tiers

**Files:**
- Modify: `eval/quality/runner.py:247-336,406-427`
- Modify: `Makefile:1,89-95`
- Test: `tests/test_quality_eval.py`

**Interfaces:**
- Consumes: `load_manifest_cases(path: Path) -> list[GoldCase]` from Task 1.
- Produces: `run(..., manifest: Path | None = None)` and CLI `--manifest`.

- [ ] **Step 1: Add a failing runner-selection test**

Add to `tests/test_quality_eval.py`:

```python
def test_runner_uses_manifest_cases_when_requested(monkeypatch, tmp_path):
    selected = [_case()]
    monkeypatch.setattr(runner, "load_manifest_cases", lambda path: selected)
    monkeypatch.setattr(runner, "load_cases", lambda path: pytest.fail("master loader used"))

    assert runner._evaluation_cases(tmp_path / "master", tmp_path / "manifest.json") == selected
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_quality_eval.py::test_runner_uses_manifest_cases_when_requested
```

Expected: FAIL because `_evaluation_cases` does not exist.

- [ ] **Step 3: Add the minimal manifest hook**

In `eval/quality/runner.py`, import `load_manifest_cases` and add:

```python
def _evaluation_cases(dataset: Path, manifest: Path | None) -> list[GoldCase]:
    return load_manifest_cases(manifest) if manifest else load_cases(dataset)
```

Change the runner signature and first line to:

```python
async def run(
    dataset: Path,
    report_dir: Path,
    top_k: int = 10,
    manifest: Path | None = None,
) -> dict[str, object]:
    cases = _evaluation_cases(dataset, manifest)
```

Record `"manifest": str(manifest) if manifest else None` in the report. Keep all current retrieval modes and metrics unchanged.

Add the CLI argument and pass it through:

```python
parser.add_argument("--manifest", type=Path)

report = asyncio.run(
    run(args.dataset, args.report_dir, top_k=args.top_k, manifest=args.manifest)
)
```

- [ ] **Step 4: Add explicit Make targets**

Add the targets to `.PHONY` and below `quality-eval`:

```make
quality-eval-100:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner \
		--manifest eval/quality/manifests/dev_100.json \
		--report-dir eval/quality/reports_retrieval_100

quality-eval-200:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner \
		--manifest eval/quality/manifests/change_200.json \
		--report-dir eval/quality/reports_retrieval_200
```

Keep `quality-eval` as the existing full 1,000-query retrieval command.

- [ ] **Step 5: Run focused tests and a no-network CLI parse check**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_eval.py
PYTHONPATH=$PWD python3 -m eval.quality.runner --help
```

Expected: tests pass and help lists `--manifest`; do not execute a retrieval benchmark yet.

- [ ] **Step 6: Commit**

```bash
git add eval/quality/runner.py tests/test_quality_eval.py Makefile
git commit -m "eval: support fast retrieval tiers"
```

---

### Task 3: End-to-end `/v2/chat` case evaluation and strict judge

**Files:**
- Create: `eval/quality/chat_runner.py`
- Create: `tests/test_chat_quality_eval.py`

**Interfaces:**
- Consumes: `GoldCase`, `normalize_url`, `app.rag.answerer._call_llm`, `app.rag.answerer._extract_json`, and an `httpx.AsyncClient` pointed at the running BuzzBot API.
- Produces: `evaluate_case(case, client) -> dict[str, object]`, `judge_answer(case, response) -> dict[str, object]`, and `summarize_results(results) -> dict[str, object]`.

- [ ] **Step 1: Write failing unit tests for HTTP mapping, strict judge parsing, and metrics**

Create `tests/test_chat_quality_eval.py` with the existing `_case` data repeated locally so the test is independent:

```python
import json

import httpx
import pytest

from eval.quality import chat_runner
from eval.quality.schema import GoldCase


def _case() -> GoldCase:
    return GoldCase(
        id="gold-001-v3",
        variant_group="gold-001",
        question="How do I order a transcript?",
        gold_answer="Use Parchment to order the official transcript.",
        gold_urls=("https://registrar.gatech.edu/current-students/transcripts",),
        gold_sources=("gt-registrar-lifecycle",),
        gold_vertical="academics",
        gold_locator="official transcript",
        question_type="process",
        time_sensitive=False,
        style="student_scenario",
    )


@pytest.mark.asyncio
async def test_evaluate_case_calls_v2_chat_and_records_gold_citation(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/chat"
        assert json.loads(request.content)["thread_id"] == "eval-gold-001-v3"
        return httpx.Response(
            200,
            json={
                "thread_id": "eval-gold-001-v3",
                "answer": "Order it through Parchment.",
                "citations": [
                    {
                        "url": "https://registrar.gatech.edu/current-students/transcripts/",
                        "quote": "Order through Parchment.",
                    }
                ],
                "confidence": 0.9,
                "notes": [],
                "freshness": {},
                "debug": {},
            },
        )

    monkeypatch.setattr(
        chat_runner,
        "judge_answer",
        AsyncMock(return_value={"verdict": "CORRECT", "supported": True, "reason": ""}),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        result = await chat_runner.evaluate_case(_case(), client)

    assert result["correct"] is True
    assert result["citation_gold_hit"] is True
    assert result["abstained"] is False


@pytest.mark.asyncio
async def test_judge_fails_closed_on_malformed_output(monkeypatch):
    monkeypatch.setattr(chat_runner, "_call_llm", AsyncMock(return_value="not json"))

    result = await chat_runner.judge_answer(
        _case(),
        {"answer": "An answer", "citations": []},
    )

    assert result["verdict"] == "ERROR"
    assert result["supported"] is False


def test_summary_counts_unsupported_confident_answer_as_unsafe():
    summary = chat_runner.summarize_results(
        [
            {
                "status": "COMPLETED",
                "correct": False,
                "supported": False,
                "abstained": False,
                "confidence": 0.9,
                "citation_gold_hit": False,
                "latency_ms": 10,
                "cost_usd": 0.001,
                "vertical": "academics",
                "question_type": "process",
                "style": "student_scenario",
                "time_sensitive": False,
            }
        ]
    )

    assert summary["unsafe_confident_answer_rate"] == 1.0
    assert summary["correct_abstention_rate"] is None
```

Add `from unittest.mock import AsyncMock`.

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_chat_quality_eval.py
```

Expected: collection fails because `eval.quality.chat_runner` does not exist.

- [ ] **Step 3: Implement the strict evaluation-only judge**

Create `eval/quality/chat_runner.py` and import the existing model call rather than another client:

```python
from app.rag.answerer import _call_llm, _extract_json
```

Implement `judge_answer` with this fixed contract:

```python
async def judge_answer(case: GoldCase, response: dict[str, object]) -> dict[str, object]:
    system = """You are a strict RAG evaluator. Use only the supplied gold answer and citation quotes.
Return JSON only: {"verdict":"CORRECT|INCORRECT|INSUFFICIENT","supported":true|false,"reason":"short reason"}.
CORRECT means the answer materially matches the gold answer without contradiction.
supported is true only when the material answer claims are supported by the citation quotes.
Missing, unrelated, or contradictory evidence is not support. Do not use outside knowledge."""
    user = json.dumps(
        {
            "question": case.question,
            "gold_answer": case.gold_answer,
            "answer": response.get("answer", ""),
            "citation_quotes": [
                citation.get("quote", "")
                for citation in response.get("citations", [])
                if isinstance(citation, dict)
            ],
        },
        ensure_ascii=False,
    )
    try:
        raw = await _call_llm(system, user, temperature=0.0, max_tokens=128)
        payload = _extract_json(raw)
        verdict = str(payload.get("verdict", "")).upper()
        supported = payload.get("supported")
        if verdict not in {"CORRECT", "INCORRECT", "INSUFFICIENT"} or not isinstance(
            supported, bool
        ):
            raise ValueError("malformed judge response")
        return {
            "verdict": verdict,
            "supported": supported,
            "reason": str(payload.get("reason", "")),
        }
    except UsageLimitExceeded:
        raise
    except Exception as exc:
        logger.debug("chat evaluation judge failed", error=type(exc).__name__)
        return {"verdict": "ERROR", "supported": False, "reason": "judge failed closed"}
```

All answer facts are answerable by construction, so an abstention is a failed answer in these tiers. `correct_abstention_rate` is reported as `null`; a meaningful rate requires a future, separately approved unanswerable dataset.

- [ ] **Step 4: Implement one-case `/v2/chat` evaluation**

Use the real endpoint and normalized gold URLs:

```python
async def evaluate_case(case: GoldCase, client: httpx.AsyncClient) -> dict[str, object]:
    started = time.perf_counter()
    response = await client.post(
        "/v2/chat",
        json={"query": case.question, "thread_id": f"eval-{case.id}"},
    )
    if response.status_code == 429 and response.json().get("detail", {}).get("error") == "usage_limit_exceeded":
        raise UsageLimitExceeded("chat evaluation budget exhausted")
    response.raise_for_status()
    body = response.json()
    citations = body.get("citations", [])
    gold_urls = {normalize_url(url) for url in case.gold_urls}
    citation_hit = any(
        normalize_url(str(citation.get("url", ""))) in gold_urls
        for citation in citations
        if isinstance(citation, dict)
    )
    abstained = not citations and float(body.get("confidence", 0.0)) <= 0.2
    judged = (
        {"verdict": "ABSTAINED", "supported": False, "reason": "answerable gold case abstained"}
        if abstained
        else await judge_answer(case, body)
    )
    return {
        "case_id": case.id,
        "variant_group": case.variant_group,
        "question": case.question,
        "gold_answer": case.gold_answer,
        "gold_urls": list(case.gold_urls),
        "vertical": case.gold_vertical,
        "question_type": case.question_type,
        "style": case.style,
        "time_sensitive": case.time_sensitive,
        "status": "COMPLETED",
        "answer": body.get("answer", ""),
        "citations": citations,
        "confidence": float(body.get("confidence", 0.0)),
        "notes": body.get("notes", []),
        "abstained": abstained,
        "correct": judged["verdict"] == "CORRECT" and judged["supported"],
        "supported": judged["supported"],
        "judge_verdict": judged["verdict"],
        "judge_reason": judged["reason"],
        "citation_gold_hit": citation_hit,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
```

Format the long 429 condition to satisfy Ruff. HTTP errors other than budget exhaustion are recorded by the run loop in Task 4; do not retry indefinitely.

- [ ] **Step 5: Implement summary metrics with simple grouped counts**

Implement `summarize_results(results)` using only completed result rows:

```python
def _ratio(rows: list[dict[str, object]], key: str) -> float:
    return sum(bool(row.get(key)) for row in rows) / len(rows) if rows else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def _basic_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    latencies = [float(row.get("latency_ms", 0.0)) for row in rows]
    return {
        "cases": len(rows),
        "answer_correctness": _ratio(rows, "correct"),
        "supported_cited_answer_rate": (
            sum(bool(row.get("supported") and row.get("citations")) for row in rows)
            / len(rows)
            if rows
            else 0.0
        ),
        "abstention_rate": _ratio(rows, "abstained"),
        "correct_abstention_rate": None,
        "unsafe_confident_answer_rate": (
            sum(
                bool(
                    not row.get("correct")
                    and not row.get("abstained")
                    and float(row.get("confidence", 0.0)) >= 0.5
                )
                for row in rows
            )
            / len(rows)
            if rows
            else 0.0
        ),
        "citation_gold_url_hit_rate": _ratio(rows, "citation_gold_hit"),
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
        "total_cost_usd": sum(float(row.get("cost_usd", 0.0)) for row in rows),
    }


def summarize_results(results: list[dict[str, object]]) -> dict[str, object]:
    rows = [row for row in results if row.get("status") == "COMPLETED"]
    summary = _basic_metrics(rows)
    breakdowns: dict[str, dict[str, object]] = {}
    for key in ("vertical", "question_type", "style", "time_sensitive"):
        buckets: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            buckets.setdefault(str(row.get(key, "unknown")), []).append(row)
        breakdowns[key] = {
            value: _basic_metrics(bucket) for value, bucket in sorted(buckets.items())
        }
    summary["breakdowns"] = breakdowns
    return summary
```

Import `math` and `statistics`; add no dependency.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_chat_quality_eval.py
```

Expected: all new unit tests pass without network or paid API calls.

- [ ] **Step 7: Commit**

```bash
git add eval/quality/chat_runner.py tests/test_chat_quality_eval.py
git commit -m "eval: add v2 chat quality checks"
```

---

### Task 4: Resume, budget stop, reports, and commands

**Files:**
- Modify: `eval/quality/chat_runner.py`
- Modify: `tests/test_chat_quality_eval.py`
- Modify: `eval/quality/README.md`
- Modify: `Makefile:1,89-95`

**Interfaces:**
- Consumes: `load_manifest_cases`, `evaluate_case`, `summarize_results`, and `get_usage`.
- Produces: `run(manifest, base_url, report_dir, force=False, limit=None, min_interval=2.6)`, CLI entry point, `quality-chat-100`, and `quality-chat-200`.

- [ ] **Step 1: Add failing resume and budget-stop tests**

Add to `tests/test_chat_quality_eval.py`:

```python
@pytest.mark.asyncio
async def test_run_resumes_completed_case_ids(monkeypatch, tmp_path):
    first = _case()
    second = replace(first, id="gold-002-v3", variant_group="gold-002")
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "latest_cases.jsonl").write_text(
        json.dumps({"case_id": first.id, "status": "COMPLETED"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chat_runner, "load_manifest_cases", lambda path: [first, second])
    evaluate = AsyncMock(
        return_value={
            "case_id": second.id,
            "status": "COMPLETED",
            "correct": True,
            "supported": True,
            "abstained": False,
            "confidence": 0.9,
            "citations": [{"url": second.gold_urls[0]}],
            "citation_gold_hit": True,
            "latency_ms": 10,
            "vertical": second.gold_vertical,
            "question_type": second.question_type,
            "style": second.style,
            "time_sensitive": False,
        }
    )
    monkeypatch.setattr(chat_runner, "evaluate_case", evaluate)

    report = await chat_runner.run(
        tmp_path / "manifest.json",
        "http://test",
        report_dir,
        min_interval=0,
    )

    assert evaluate.await_count == 1
    assert evaluate.await_args.args[0].id == second.id
    assert report["completed"] == 2


@pytest.mark.asyncio
async def test_run_stops_cleanly_when_usage_budget_is_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_runner, "load_manifest_cases", lambda path: [_case()])
    monkeypatch.setattr(
        chat_runner,
        "evaluate_case",
        AsyncMock(side_effect=UsageLimitExceeded("limit")),
    )

    report = await chat_runner.run(
        tmp_path / "manifest.json",
        "http://test",
        tmp_path / "report",
        min_interval=0,
    )

    assert report["stop_reason"] == "USAGE_LIMIT_EXCEEDED"
    assert report["remaining"] == 1
```

Add `from dataclasses import replace` and `from app.core.usage import UsageLimitExceeded`.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_chat_quality_eval.py::test_run_resumes_completed_case_ids \
  tests/test_chat_quality_eval.py::test_run_stops_cleanly_when_usage_budget_is_exhausted
```

Expected: FAIL because `run` does not exist.

- [ ] **Step 3: Implement sequential resume and cost accounting**

Add `run` to `chat_runner.py`:

```python
async def run(
    manifest: Path,
    base_url: str,
    report_dir: Path,
    *,
    force: bool = False,
    limit: int | None = None,
    min_interval: float = 2.6,
) -> dict[str, object]:
    cases = load_manifest_cases(manifest)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        cases = cases[:limit]
    report_dir.mkdir(parents=True, exist_ok=True)
    results_path = report_dir / "latest_cases.jsonl"
    if force:
        results_path.write_text("", encoding="utf-8")
    latest = {
        str(row["case_id"]): row
        for row in _read_results(results_path)
        if row.get("case_id")
    }
    completed = {
        case_id for case_id, row in latest.items() if row.get("status") == "COMPLETED"
    }
    stop_reason = None
    last_started = 0.0

    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
        for case in cases:
            if case.id in completed:
                continue
            wait = max(0.0, min_interval - (time.monotonic() - last_started))
            if wait:
                await asyncio.sleep(wait)
            last_started = time.monotonic()
            before = float(get_usage()["total_cost"])
            try:
                row = await evaluate_case(case, client)
            except UsageLimitExceeded:
                stop_reason = "USAGE_LIMIT_EXCEEDED"
                break
            except Exception as exc:
                row = _failed_row(case, type(exc).__name__)
            row["cost_usd"] = max(0.0, float(get_usage()["total_cost"]) - before)
            _append_result(results_path, row)
            latest[case.id] = row

    relevant = [latest[case.id] for case in cases if case.id in latest]
    summary = summarize_results(relevant)
    report = {
        "benchmark": manifest.stem,
        "manifest": str(manifest),
        "planned": len(cases),
        "completed": sum(row.get("status") == "COMPLETED" for row in relevant),
        "remaining": len(cases) - sum(row.get("status") == "COMPLETED" for row in relevant),
        "stop_reason": stop_reason,
        "metrics": summary,
    }
    _write_reports(report_dir, report)
    return report
```

Add the exact local helpers below. JSONL writes flush after each row, and only the latest row for a case contributes to the summary:

```python
def _read_results(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _append_result(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def _failed_row(case: GoldCase, error: str) -> dict[str, object]:
    return {
        "case_id": case.id,
        "variant_group": case.variant_group,
        "question": case.question,
        "gold_answer": case.gold_answer,
        "gold_urls": list(case.gold_urls),
        "vertical": case.gold_vertical,
        "question_type": case.question_type,
        "style": case.style,
        "time_sensitive": case.time_sensitive,
        "status": "FAILED",
        "error": error,
        "answer": "",
        "citations": [],
        "confidence": 0.0,
        "abstained": False,
        "correct": False,
        "supported": False,
        "citation_gold_hit": False,
        "latency_ms": 0.0,
    }


def _write_reports(report_dir: Path, report: dict[str, object]) -> None:
    (report_dir / "latest_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    metrics = report["metrics"]
    markdown = "\n".join(
        [
            "# BuzzBot `/v2/chat` quality report",
            "",
            f"- Planned: {report['planned']}",
            f"- Completed: {report['completed']}",
            f"- Remaining: {report['remaining']}",
            f"- Stop reason: {report['stop_reason'] or 'none'}",
            f"- Answer correctness: {metrics['answer_correctness']:.2%}",
            f"- Supported and cited: {metrics['supported_cited_answer_rate']:.2%}",
            f"- Abstention rate: {metrics['abstention_rate']:.2%}",
            f"- Unsafe confident answers: {metrics['unsafe_confident_answer_rate']:.2%}",
            f"- Gold citation hit: {metrics['citation_gold_url_hit_rate']:.2%}",
            f"- Cost: ${metrics['total_cost_usd']:.6f}",
            "",
        ]
    )
    (report_dir / "latest_summary.md").write_text(markdown, encoding="utf-8")
```

Sequential execution is intentional: it respects the existing per-client guardrails, makes file-based usage deltas deterministic, and avoids adding coordination code. The 100-case tier is the speed boundary.

- [ ] **Step 4: Add CLI arguments and explicit targets**

Add the CLI entry point to `chat_runner.py`:

```python
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BuzzBot /v2/chat quality evaluation")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report-dir", type=Path, default=Path("eval/quality/reports_chat"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-interval", type=float, default=2.6)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = asyncio.run(
        run(
            args.manifest,
            args.base_url,
            args.report_dir,
            force=args.force,
            limit=args.limit,
            min_interval=args.min_interval,
        )
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["remaining"] == 0 and report["stop_reason"] is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

Add Make targets, but deliberately omit a 1,000-case chat target:

```make
quality-chat-100:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.chat_runner \
		--manifest eval/quality/manifests/dev_100.json \
		--report-dir eval/quality/reports_chat_100

quality-chat-200:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.chat_runner \
		--manifest eval/quality/manifests/change_200.json \
		--report-dir eval/quality/reports_chat_200
```

- [ ] **Step 5: Document exact operating commands**

Update `eval/quality/README.md` with:

```bash
# Fast retrieval loop; uses embeddings but no chat completion
make quality-eval-100

# Material-change retrieval gate
make quality-eval-200

# Full retrieval release gate only
make quality-eval

# In another terminal, start the API before a live chat evaluation
make run-backend

# Actual gpt-4o-mini + /v2/chat evaluation; resumes automatically
make quality-chat-100

# Only after a material change
make quality-chat-200
```

State explicitly that all current gold cases are answerable, so abstention is a failure and `correct_abstention_rate` is `null` until an unanswerable benchmark is separately approved.

- [ ] **Step 6: Run focused tests and lint**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q tests/test_quality_eval.py tests/test_chat_quality_eval.py
ruff check eval/quality tests/test_quality_eval.py tests/test_chat_quality_eval.py
ruff format --check eval/quality tests/test_quality_eval.py tests/test_chat_quality_eval.py
```

Expected: all focused tests and Ruff checks pass without network or paid API calls.

- [ ] **Step 7: Commit**

```bash
git add eval/quality/chat_runner.py tests/test_chat_quality_eval.py eval/quality/README.md Makefile
git commit -m "eval: add resumable chat quality runner"
```

---

### Task 5: Full verification and bounded real smoke

**Files:**
- Verify only; do not commit generated reports.

**Interfaces:**
- Consumes: all prior tasks and the existing local `.env`, PostgreSQL database, OpenAI key, and `$3` usage guard.
- Produces: test evidence plus a maximum two-case live smoke report outside the repository.

- [ ] **Step 1: Verify identity and working-tree scope**

Run:

```bash
git config user.name
git config user.email
git status --short
```

Expected identity: `tkim602` and `tkim602@gatech.edu`. Confirm the pre-existing untracked report directories and `schema.py.bak` remain untouched.

- [ ] **Step 2: Run the complete automated suite**

Run:

```bash
make test
make test-db
make lint
```

Expected: the full unit suite, DB integration suite, and Ruff checks pass.

- [ ] **Step 3: Check remaining budget before live verification**

Run:

```bash
make usage
```

Expected: total is below `$3.00`. If the limit is already reached, stop and report the blocker without making an API call.

- [ ] **Step 4: Start the production API**

In a persistent terminal session, run:

```bash
make run-backend
```

Wait for the local server to become ready, then verify `http://127.0.0.1:8000/ready` returns success.

- [ ] **Step 5: Run exactly two live cases into a temporary directory**

Run:

```bash
buzzbot_eval_tmp=$(mktemp -d)
PYTHONPATH=$PWD python3 -m eval.quality.chat_runner \
  --manifest eval/quality/manifests/dev_100.json \
  --report-dir "$buzzbot_eval_tmp" \
  --limit 2
```

Expected summary: `planned=2`, `completed=2`, `remaining=0`, and `stop_reason=null`. This is the only paid live evaluation performed during implementation. Do not run the 100-, 200-, or 1,000-case live target.

- [ ] **Step 6: Check usage and repository cleanliness**

Run:

```bash
make usage
git status --short
git log -5 --oneline
```

Expected: cost remains below `$3.00`; only the pre-existing untracked artifacts remain; implementation commits are authored by `tkim602`.

- [ ] **Step 7: Final handoff**

Report:

- changed files and commits;
- exact test, DB integration, and lint results;
- two-case live smoke status and cost delta;
- `make quality-eval-100` for fast retrieval;
- `make quality-chat-100` for the manual live development gate;
- confirmation that no full live 100/200/1,000 chat run was executed.
