import json
from unittest.mock import AsyncMock

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
        AsyncMock(
            return_value={"verdict": "CORRECT", "supported": True, "reason": ""}
        ),
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
