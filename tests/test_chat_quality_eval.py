import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.usage import UsageLimitExceeded
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
async def test_evaluate_case_calls_chat_and_records_gold_citation(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat"
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
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
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
    monkeypatch.setattr(chat_runner, "evaluate_case", AsyncMock(return_value=budget_row))

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
    evaluate = AsyncMock(side_effect=[httpx.HTTPError("broken"), judge_failed, completed])
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
        {
            "timestamp": "t1",
            "model": "gpt-4o-mini",
            "type": "input",
            "tokens": 100,
            "cost": 0.04,
        },
        {
            "timestamp": "t2",
            "model": "gpt-4o-mini",
            "type": "output",
            "tokens": 20,
            "cost": 0.03,
        },
        {
            "timestamp": "t3",
            "model": "gpt-4o-mini",
            "type": "input",
            "tokens": 50,
            "cost": 0.05,
        },
        {
            "timestamp": "t4",
            "model": "gpt-4o-mini",
            "type": "output",
            "tokens": 10,
            "cost": 0.03,
        },
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
