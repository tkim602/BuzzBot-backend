# BuzzBot `/v2/chat` Quality Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. This project uses inline execution; do not dispatch subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fast, fixed 100/200-case retrieval gates and a resumable end-to-end `/v2/chat` evaluation that uses the configured `gpt-4o-mini` without automatically running all 1,000 live questions.

**Architecture:** Keep the existing 1,000-query verified dataset as the immutable master. Versioned manifests contain concrete case IDs for the 100- and 200-case tiers, and the 200-case tier is a strict superset of the 100-case tier. The current retrieval runner consumes those selections, while one new evaluation module calls the real `/v2/chat` HTTP endpoint sequentially, judges completed answers with the existing configured LLM and shared usage accounting, appends resumable JSONL results, and renders a compact summary.

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

- Create `eval/quality/manifests/dev_100.json`: 100 explicit case IDs, one per fact.
- Create `eval/quality/manifests/change_200.json`: 200 explicit case IDs, two per fact and all dev IDs included.
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
    assert len({case.id for case in cases}) == 100


def test_change_manifest_selects_two_fixed_cases_per_fact():
    cases = load_manifest_cases(Path("eval/quality/manifests/change_200.json"))

    assert len(cases) == 200
    counts = Counter(case.variant_group for case in cases)
    assert len(counts) == 100
    assert set(counts.values()) == {2}
    assert len({case.id for case in cases}) == 200


def test_change_manifest_contains_all_dev_cases():
    dev = load_manifest_cases(Path("eval/quality/manifests/dev_100.json"))
    change = load_manifest_cases(Path("eval/quality/manifests/change_200.json"))

    assert {case.id for case in dev} <= {case.id for case in change}


def test_manifest_fails_when_a_case_id_is_unknown(tmp_path):
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
                "version": 1,
                "name": "broken",
                "master_dataset": "dataset",
                "case_ids": ["gold-001-v2"],
                "expected_fact_count": 1,
                "cases_per_fact": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown case ids"):
        load_manifest_cases(manifest)
```

Also add `from collections import Counter` at the top.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_quality_eval.py::test_dev_manifest_selects_one_fixed_case_per_fact \
  tests/test_quality_eval.py::test_change_manifest_selects_two_fixed_cases_per_fact \
  tests/test_quality_eval.py::test_change_manifest_contains_all_dev_cases \
  tests/test_quality_eval.py::test_manifest_fails_when_a_case_id_is_unknown
```

Expected: collection fails because `load_manifest_cases` and the manifest files do not exist.

- [ ] **Step 3: Add the two explicit versioned manifests**

Produce the complete concrete manifests once from the verified 2026-08-22 production report using this read-only command, then add its two JSON outputs with `apply_patch`:

```bash
python3 - <<'PY'
import json
from collections import defaultdict
from pathlib import Path

report = Path("eval/quality/reports_verified_after_corpus_fix/latest_cases.jsonl")
rows = [json.loads(line) for line in report.read_text().splitlines() if line.strip()]
groups = defaultdict(list)
for row in rows:
    if row.get("mode") == "production":
        groups[row["variant_group"]].append(row)

wording_priority = {
    "v10": 9,  # student chat
    "v7": 8,   # casual
    "v3": 7,   # scenario
    "v9": 6,   # decision first
    "v5": 5,   # indirect
    "v2": 4,   # natural
    "v8": 3,   # confirmation
    "v4": 2,   # concise
    "v1": 1,   # direct
    "v6": 0,   # keyword
}


def score(row):
    rank = row.get("rank")
    missed = rank is None or rank > 5
    severity = 999 if rank is None else rank
    suffix = row["case_id"].rsplit("-", 1)[-1]
    return missed, severity, wording_priority[suffix]


dev = []
change = []
for group in sorted(groups):
    realistic = [row for row in groups[group] if not row["case_id"].endswith("-v6")]
    selected = max(realistic or groups[group], key=score)["case_id"]
    second = f"{group}-v1" if selected.endswith("-v2") else f"{group}-v2"
    dev.append(selected)
    change.extend((selected, second))

assert len(dev) == len(set(dev)) == 100
assert len(change) == len(set(change)) == 200
assert set(dev) < set(change)

for name, ids, cases_per_fact in (
    ("dev_100", dev, 1),
    ("change_200", change, 2),
):
    print(f"### eval/quality/manifests/{name}.json")
    print(
        json.dumps(
            {
                "version": 1,
                "name": f"buzzbot_gt_public_{name}",
                "master_dataset": "../data_verified",
                "expected_fact_count": 100,
                "cases_per_fact": cases_per_fact,
                "case_ids": ids,
            },
            indent=2,
        )
    )
PY
```

The one-time selection order encoded above is:

1. a case that missed production Hit@5;
2. among misses, the most difficult realistic wording;
3. when every variant succeeded, the realistic variant with the worst rank;
4. when ranks tie, prefer student-chat, casual, scenario, decision, indirect, natural, confirmation, concise, direct, then keyword wording.

Commit the resulting IDs; the loader never recomputes them from a report.

Create `change_200.json` with `cases_per_fact: 2` and 200 concrete IDs. For each fact include its exact dev ID plus one natural second wording. Use `v2` for the second wording unless the dev ID is already `v2`, then use `v1`. This guarantees `dev_100` is a strict subset without making the dev selection positional.

- [ ] **Step 4: Implement strict manifest loading**

Add this function below `load_cases` in `eval/quality/schema.py`:

```python
def load_manifest_cases(path: Path) -> list[GoldCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    case_ids = tuple(str(value) for value in payload.get("case_ids", []))
    expected_facts = int(payload.get("expected_fact_count", 0))
    cases_per_fact = int(payload.get("cases_per_fact", 0))
    master_value = payload.get("master_dataset")
    if (
        payload.get("version") != 1
        or not isinstance(master_value, str)
        or not case_ids
        or expected_facts < 1
        or cases_per_fact < 1
    ):
        raise ValueError(f"{path}: invalid evaluation manifest")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError(f"{path}: duplicate case id")

    master = (path.parent / master_value).resolve()
    by_id = {case.id: case for case in load_cases(master)}
    if unknown := sorted(set(case_ids) - set(by_id)):
        raise ValueError(f"{path}: unknown case ids: {', '.join(unknown)}")
    selected = [by_id[case_id] for case_id in case_ids]
    counts = Counter(case.variant_group for case in selected)
    if len(counts) != expected_facts or set(counts.values()) != {cases_per_fact}:
        raise ValueError(f"{path}: manifest selection is incomplete")
    return selected
```

Add `from collections import Counter` to `eval/quality/schema.py`. Preserve the master dataset's separate dimensions with these exact field changes:

```python
@dataclass(frozen=True)
class GoldCase:
    # existing fields remain unchanged
    difficulty: str
    style: str
```

In `_load_query_level_json`, pass:

```python
difficulty=str(raw.get("difficulty") or "unknown"),
style=str(raw.get("style") or "unknown"),
```

In `_expand_fact`, pass `difficulty="generated"`. Add `difficulty="direct"` to the `_case` test helper. Do not modify the master JSON files.

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

Add `"difficulty": grouped_summary(production, "difficulty")` beside the existing style breakdown, and include `"difficulty": result.case.difficulty` in `_case_payload`. Difficulty and style remain separate report dimensions.

Add the CLI argument and pass it through:

```python
parser.add_argument("--manifest", type=Path)

report = asyncio.run(
    run(args.dataset, args.report_dir, top_k=args.top_k, manifest=args.manifest)
)
```

- [ ] **Step 4: Add explicit Make targets**

Replace the single ambiguous target with explicit retrieval tiers and add them to `.PHONY`:

```make
quality-retrieval-dev:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner \
		--manifest eval/quality/manifests/dev_100.json \
		--report-dir eval/quality/reports_retrieval_100

quality-retrieval-change:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner \
		--manifest eval/quality/manifests/change_200.json \
		--report-dir eval/quality/reports_retrieval_200

quality-retrieval-full:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner \
		--dataset eval/quality/data_verified
```

Do not add a `--tier release` shortcut.

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
        difficulty="student_scenario",
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


def test_summary_leaves_confidence_policy_unset_before_baseline():
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

    assert summary["unsafe_confident_answer_rate"] is None
    assert summary["correct_abstention_rate"] is None


def test_abstention_uses_production_note_not_confidence_threshold():
    assert chat_runner._is_abstention(
        {"notes": ["Strict cite-or-abstain policy applied."], "confidence": 0.9}
    )
    assert not chat_runner._is_abstention({"notes": [], "confidence": 0.1})


def test_summary_separates_correctness_support_and_all_attempt_cost():
    common = {
        "abstained": False,
        "confidence": 0.9,
        "citation_gold_hit": True,
        "latency_ms": 10,
        "vertical": "academics",
        "question_type": "process",
        "difficulty": "student_scenario",
        "style": "scenario",
        "time_sensitive": False,
    }
    summary = chat_runner.summarize_results(
        [
            {
                **common,
                "status": "COMPLETED",
                "correct": True,
                "supported": False,
                "citations": [],
                "cost_usd": 0.01,
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "usage_attribution_valid": True,
            },
            {
                **common,
                "status": "JUDGE_FAILED",
                "correct": False,
                "supported": False,
                "citations": [],
                "cost_usd": 0.02,
                "input_tokens": 50,
                "output_tokens": 10,
                "total_tokens": 60,
                "usage_attribution_valid": True,
            },
        ]
    )

    assert summary["answer_correctness"] == 1.0
    assert summary["evidence_support_rate"] == 0.0
    assert summary["total_cost_usd"] == pytest.approx(0.03)
    assert summary["input_tokens"] == 150
    assert summary["total_tokens"] == 180
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
    if (
        response.status_code == 429
        and response.json().get("detail", {}).get("error") == "usage_limit_exceeded"
    ):
        return {
            **_case_fields(case),
            "status": "CHAT_BUDGET_EXHAUSTED",
            "answer": "",
            "citations": [],
            "confidence": 0.0,
            "notes": [],
            "abstained": False,
            "correct": False,
            "supported": False,
            "judgment": None,
            "citation_gold_hit": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    response.raise_for_status()
    body = response.json()
    citations = body.get("citations", [])
    gold_urls = {normalize_url(url) for url in case.gold_urls}
    citation_hit = any(
        normalize_url(str(citation.get("url", ""))) in gold_urls
        for citation in citations
        if isinstance(citation, dict)
    )
    abstained = _is_abstention(body)
    try:
        judged = (
            {
                "verdict": "ABSTAINED",
                "supported": False,
                "reason": "answerable gold case abstained",
            }
            if abstained
            else await judge_answer(case, body)
        )
    except UsageLimitExceeded:
        return {
            **_case_fields(case),
            "status": "JUDGE_BUDGET_EXHAUSTED",
            "answer": body.get("answer", ""),
            "citations": citations,
            "confidence": float(body.get("confidence", 0.0)),
            "notes": body.get("notes", []),
            "abstained": abstained,
            "correct": False,
            "supported": False,
            "judgment": None,
            "citation_gold_hit": citation_hit,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    status = "JUDGE_FAILED" if judged["verdict"] == "ERROR" else "COMPLETED"
    return {
        **_case_fields(case),
        "status": status,
        "answer": body.get("answer", ""),
        "citations": citations,
        "confidence": float(body.get("confidence", 0.0)),
        "notes": body.get("notes", []),
        "abstained": abstained,
        "correct": judged["verdict"] == "CORRECT",
        "supported": judged["supported"],
        "judgment": judged,
        "citation_gold_hit": citation_hit,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
```

Add this local helper above `evaluate_case`:

```python
def _case_fields(case: GoldCase) -> dict[str, object]:
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
        "difficulty": case.difficulty,
    }
```

Use the production graph's explicit note as the deterministic abstention signal:

```python
_ABSTENTION_NOTE = "Strict cite-or-abstain policy applied."


def _is_abstention(response: dict[str, object]) -> bool:
    notes = response.get("notes", [])
    return isinstance(notes, list) and _ABSTENTION_NOTE in notes
```

HTTP errors other than budget exhaustion are recorded by the run loop in Task 4; do not retry indefinitely.

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
    confidences = [float(row.get("confidence", 0.0)) for row in rows]
    return {
        "cases": len(rows),
        "answer_correctness": _ratio(rows, "correct"),
        "evidence_support_rate": _ratio(rows, "supported"),
        "supported_cited_answer_rate": (
            sum(bool(row.get("supported") and row.get("citations")) for row in rows)
            / len(rows)
            if rows
            else 0.0
        ),
        "abstention_rate": _ratio(rows, "abstained"),
        "correct_abstention_rate": None,
        "confidence_threshold": None,
        "unsafe_confident_answer_rate": None,
        "citation_gold_url_hit_rate": _ratio(rows, "citation_gold_hit"),
        "confidence": {
            "p50": statistics.median(confidences) if confidences else 0.0,
            "p95": _percentile(confidences, 0.95),
        },
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
    }


def summarize_results(results: list[dict[str, object]]) -> dict[str, object]:
    rows = [row for row in results if row.get("status") == "COMPLETED"]
    summary = _basic_metrics(rows)
    breakdowns: dict[str, dict[str, object]] = {}
    for key in ("vertical", "question_type", "difficulty", "style", "time_sensitive"):
        buckets: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            buckets.setdefault(str(row.get(key, "unknown")), []).append(row)
        breakdowns[key] = {
            value: _basic_metrics(bucket) for value, bucket in sorted(buckets.items())
        }
    summary["breakdowns"] = breakdowns
    summary["attempted_cases"] = len(results)
    usage_valid = all(
        row.get("usage_attribution_valid", False) for row in results
    )
    summary["usage_attribution_valid"] = usage_valid
    summary["total_cost_usd"] = (
        sum(float(row.get("cost_usd") or 0.0) for row in results)
        if usage_valid
        else None
    )
    for key in ("input_tokens", "output_tokens", "embedding_tokens", "total_tokens"):
        summary[key] = (
            sum(int(row.get(key, 0) or 0) for row in results)
            if usage_valid
            else None
        )
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
- Produces: `run(manifest, base_url, report_dir, force=False, limit=None, min_interval=2.6)`, CLI entry point, `quality-chat-dev`, and `quality-chat-change`.

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
async def test_run_records_chat_budget_rejection_then_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_runner, "load_manifest_cases", lambda path: [_case()])
    budget_row = {
        **chat_runner._case_fields(_case()),
        "status": "CHAT_BUDGET_EXHAUSTED",
        "answer": "",
        "citations": [],
        "confidence": 0.0,
        "judgment": None,
    }
    monkeypatch.setattr(
        chat_runner,
        "evaluate_case",
        AsyncMock(return_value=budget_row),
    )

    report = await chat_runner.run(
        tmp_path / "manifest.json",
        "http://test",
        tmp_path / "report",
        min_interval=0,
    )

    rows = chat_runner._read_results(tmp_path / "report" / "latest_cases.jsonl")
    assert rows[-1]["status"] == "CHAT_BUDGET_EXHAUSTED"
    assert report["stop_reason"] == "CHAT_BUDGET_EXHAUSTED"
    assert report["remaining"] == 1


@pytest.mark.asyncio
async def test_judge_budget_rejection_preserves_production_answer(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "thread_id": "eval-gold-001-v3",
                "answer": "Order it through Parchment.",
                "citations": [
                    {
                        "url": _case().gold_urls[0],
                        "quote": "Order it through Parchment.",
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
        AsyncMock(side_effect=UsageLimitExceeded("limit")),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        row = await chat_runner.evaluate_case(_case(), client)

    assert row["status"] == "JUDGE_BUDGET_EXHAUSTED"
    assert row["answer"] == "Order it through Parchment."
    assert row["citations"]
    assert row["judgment"] is None


@pytest.mark.asyncio
async def test_run_records_judge_budget_result_then_stops(monkeypatch, tmp_path):
    row = {
        **chat_runner._case_fields(_case()),
        "status": "JUDGE_BUDGET_EXHAUSTED",
        "answer": "Production answer preserved.",
        "citations": [{"url": _case().gold_urls[0]}],
        "confidence": 0.9,
        "judgment": None,
    }
    monkeypatch.setattr(chat_runner, "load_manifest_cases", lambda path: [_case()])
    monkeypatch.setattr(chat_runner, "evaluate_case", AsyncMock(return_value=row))

    report = await chat_runner.run(
        tmp_path / "manifest.json",
        "http://test",
        tmp_path / "report",
        min_interval=0,
    )

    stored = chat_runner._read_results(tmp_path / "report" / "latest_cases.jsonl")
    assert stored[-1]["answer"] == "Production answer preserved."
    assert stored[-1]["judgment"] is None
    assert report["stop_reason"] == "JUDGE_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_general_case_error_is_recorded_and_next_case_runs(monkeypatch, tmp_path):
    first = _case()
    second = replace(first, id="gold-002-v3", variant_group="gold-002")
    third = replace(first, id="gold-003-v3", variant_group="gold-003")
    monkeypatch.setattr(chat_runner, "load_manifest_cases", lambda path: [first, second, third])
    judge_failed = {
        **chat_runner._case_fields(second),
        "status": "JUDGE_FAILED",
        "answer": "Production answer preserved.",
        "citations": [{"url": second.gold_urls[0]}],
        "confidence": 0.8,
        "correct": False,
        "supported": False,
        "abstained": False,
        "judgment": None,
        "citation_gold_hit": True,
        "latency_ms": 10.0,
    }
    completed = {
        **chat_runner._case_fields(third),
        "status": "COMPLETED",
        "answer": "Supported answer.",
        "citations": [{"url": third.gold_urls[0]}],
        "confidence": 0.9,
        "correct": True,
        "supported": True,
        "abstained": False,
        "citation_gold_hit": True,
        "latency_ms": 10.0,
    }
    evaluate = AsyncMock(
        side_effect=[httpx.HTTPError("broken"), judge_failed, completed]
    )
    monkeypatch.setattr(chat_runner, "evaluate_case", evaluate)

    report = await chat_runner.run(
        tmp_path / "manifest.json",
        "http://test",
        tmp_path / "report",
        min_interval=0,
    )

    assert evaluate.await_count == 3
    assert report["completed"] == 1
    assert report["stop_reason"] is None


@pytest.mark.asyncio
async def test_cost_delta_includes_chat_and_judge_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_runner, "load_manifest_cases", lambda path: [_case()])
    completed = {
        **chat_runner._case_fields(_case()),
        "status": "COMPLETED",
        "answer": "Supported answer.",
        "citations": [{"url": _case().gold_urls[0]}],
        "confidence": 0.9,
        "correct": True,
        "supported": True,
        "abstained": False,
        "citation_gold_hit": True,
        "latency_ms": 10.0,
    }
    monkeypatch.setattr(chat_runner, "evaluate_case", AsyncMock(return_value=completed))
    old = {
        "timestamp": "t0",
        "model": "gpt-4o-mini",
        "type": "input",
        "tokens": 10,
        "cost": 0.10,
    }
    new_entries = [
        {"timestamp": "t1", "model": "gpt-4o-mini", "type": "input", "tokens": 100, "cost": 0.04},
        {"timestamp": "t2", "model": "gpt-4o-mini", "type": "output", "tokens": 20, "cost": 0.03},
        {"timestamp": "t3", "model": "gpt-4o-mini", "type": "input", "tokens": 50, "cost": 0.05},
        {"timestamp": "t4", "model": "gpt-4o-mini", "type": "output", "tokens": 10, "cost": 0.03},
    ]
    monkeypatch.setattr(
        chat_runner,
        "get_usage",
        MagicMock(
            side_effect=[
                {"total_cost": 0.10, "history": [old]},
                {"total_cost": 0.25, "history": [old, *new_entries]},
            ]
        ),
    )

    await chat_runner.run(
        tmp_path / "manifest.json",
        "http://test",
        tmp_path / "report",
        min_interval=0,
    )

    rows = chat_runner._read_results(tmp_path / "report" / "latest_cases.jsonl")
    assert rows[-1]["cost_usd"] == pytest.approx(0.15)
    assert rows[-1]["input_tokens"] == 150
    assert rows[-1]["output_tokens"] == 30
    assert rows[-1]["total_tokens"] == 180
```

Add `from dataclasses import replace`, `from unittest.mock import AsyncMock, MagicMock`, and `from app.core.usage import UsageLimitExceeded`.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_chat_quality_eval.py::test_run_resumes_completed_case_ids \
  tests/test_chat_quality_eval.py::test_run_records_chat_budget_rejection_then_stops \
  tests/test_chat_quality_eval.py::test_judge_budget_rejection_preserves_production_answer \
  tests/test_chat_quality_eval.py::test_run_records_judge_budget_result_then_stops \
  tests/test_chat_quality_eval.py::test_general_case_error_is_recorded_and_next_case_runs \
  tests/test_chat_quality_eval.py::test_cost_delta_includes_chat_and_judge_usage
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
            before = get_usage()
            try:
                row = await evaluate_case(case, client)
            except Exception as exc:
                row = _failed_row(case, type(exc).__name__)
            row.update(_usage_delta(before, get_usage()))
            _append_result(results_path, row)
            latest[case.id] = row
            if row["status"] in {
                "CHAT_BUDGET_EXHAUSTED",
                "JUDGE_BUDGET_EXHAUSTED",
            }:
                stop_reason = str(row["status"])
                break

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

The current usage store exposes token counts through its bounded `history`, not top-level counters. Attribute the new history entries between the before/after snapshots:

```python
def _usage_delta(
    before: dict[str, object], after: dict[str, object]
) -> dict[str, object]:
    before_history = list(before.get("history", []))
    after_history = list(after.get("history", []))
    if before_history:
        marker = before_history[-1]
        matches = [
            index for index, entry in enumerate(after_history) if entry == marker
        ]
        if not matches:
            return {
                "usage_attribution_valid": False,
                "cost_usd": None,
                "input_tokens": None,
                "output_tokens": None,
                "embedding_tokens": None,
                "total_tokens": None,
            }
        entries = after_history[matches[-1] + 1 :]
    else:
        entries = after_history

    def tokens(usage_type: str) -> int:
        return sum(
            int(entry.get("tokens", 0))
            for entry in entries
            if entry.get("type") == usage_type
        )

    input_tokens = tokens("input")
    output_tokens = tokens("output")
    embedding_tokens = tokens("embedding")
    return {
        "usage_attribution_valid": True,
        "cost_usd": sum(float(entry.get("cost", 0.0)) for entry in entries),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "embedding_tokens": embedding_tokens,
        "total_tokens": input_tokens + output_tokens + embedding_tokens,
    }
```

Sequential execution ensures the new history slice contains the production chat and its judge for only this case. If the marker disappears because another process resets or rewrites usage history, preserve the case result but mark attribution invalid rather than reporting false zero usage.

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
        **_case_fields(case),
        "status": "FAILED",
        "error": error,
        "answer": "",
        "citations": [],
        "confidence": 0.0,
        "abstained": False,
        "correct": False,
        "supported": False,
        "judgment": None,
        "citation_gold_hit": False,
        "latency_ms": 0.0,
    }


def _write_reports(report_dir: Path, report: dict[str, object]) -> None:
    (report_dir / "latest_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    metrics = report["metrics"]
    cost = metrics["total_cost_usd"]
    cost_label = f"${cost:.6f}" if isinstance(cost, int | float) else "unavailable"
    markdown = "\n".join(
        [
            "# BuzzBot `/v2/chat` quality report",
            "",
            f"- Planned: {report['planned']}",
            f"- Completed: {report['completed']}",
            f"- Remaining: {report['remaining']}",
            f"- Stop reason: {report['stop_reason'] or 'none'}",
            f"- Answer correctness: {metrics['answer_correctness']:.2%}",
            f"- Evidence support: {metrics['evidence_support_rate']:.2%}",
            f"- Supported and cited: {metrics['supported_cited_answer_rate']:.2%}",
            f"- Abstention rate: {metrics['abstention_rate']:.2%}",
            "- Unsafe confident answers: not scored until baseline threshold is frozen",
            f"- Gold citation hit: {metrics['citation_gold_url_hit_rate']:.2%}",
            f"- Cost: {cost_label}",
            f"- Input tokens: {metrics['input_tokens']}",
            f"- Output tokens: {metrics['output_tokens']}",
            f"- Total tokens: {metrics['total_tokens']}",
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

Add Make targets, but deliberately omit `quality-chat-full`:

```make
quality-chat-dev:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.chat_runner \
		--manifest eval/quality/manifests/dev_100.json \
		--report-dir eval/quality/reports_chat_100

quality-chat-change:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.chat_runner \
		--manifest eval/quality/manifests/change_200.json \
		--report-dir eval/quality/reports_chat_200
```

- [ ] **Step 5: Document exact operating commands**

Update `eval/quality/README.md` with:

```bash
# Fast retrieval loop; uses embeddings but no chat completion
make quality-retrieval-dev

# Material-change retrieval gate
make quality-retrieval-change

# Full retrieval release gate only
make quality-retrieval-full

# In another terminal, start the API before a live chat evaluation
make run-backend

# Actual gpt-4o-mini + /v2/chat evaluation; resumes automatically
make quality-chat-dev

# Only after a material change
make quality-chat-change
```

State explicitly that all current gold cases are answerable, so abstention is a failure and `correct_abstention_rate` is `null` until an unanswerable benchmark is separately approved.
Also state that `unsafe_confident_answer_rate` and `confidence_threshold` remain `null` for the first baseline; raw case confidence and p50/p95 are recorded so a later reviewed threshold can be frozen without changing historical results.

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
- `make quality-retrieval-dev` for fast retrieval;
- `make quality-chat-dev` for the manual live development gate;
- confirmation that no full live 100/200/1,000 chat run was executed.
