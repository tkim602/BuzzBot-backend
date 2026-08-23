from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langsmith.evaluation import EvaluationResult

from eval.langsmith.datasets import DATASET_NAME, ensure_dataset, load_course_details
from eval.langsmith.evaluators import score_stages, stage_evaluator
from eval.langsmith.failure_stage import classify_failure
from eval.langsmith.run_course_details import (
    semantic_and_failure_evaluator,
    summarize_rows,
)


def _dataset(path: Path, count: int = 20) -> Path:
    items = []
    for index in range(count):
        items.append(
            {
                "id": f"fd-course-{index + 1:03d}",
                "variant_group": f"fd-course-{index + 1:03d}",
                "subsystem": "course_details",
                "question": f"What is CS {6000 + index}?",
                "expected_route": "course_details",
                "expected": {
                    "course_code": f"CS {6000 + index}",
                    "gold_urls": ["https://catalog.gatech.edu/coursesaz/cs/"],
                    "gold_sources": ["gt-catalog"],
                },
                "gold_answer": "Official catalog description.",
                "gold_urls": ["https://catalog.gatech.edu/coursesaz/cs/"],
                "gold_sources": ["gt-catalog"],
                "gold_vertical": "academics",
                "question_type": "course_detail",
                "difficulty": "direct",
                "style": "direct",
                "time_sensitive": False,
            }
        )
    items.append({"id": "policy-1", "subsystem": "policy_rag"})
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    return path


def test_course_details_loader_preserves_exactly_twenty_frozen_cases(tmp_path):
    cases = load_course_details(_dataset(tmp_path / "full_domain_500.json"))

    assert len(cases) == 20
    assert cases[0].case_id == "fd-course-001"
    assert cases[0].expected_subject == "CS"
    assert cases[0].expected_course_number == "6000"
    assert cases[0].gold_urls == ("https://catalog.gatech.edu/coursesaz/cs/",)


def test_course_details_loader_fails_when_frozen_slice_is_incomplete(tmp_path):
    with pytest.raises(ValueError, match="expected 20 course_details cases"):
        load_course_details(_dataset(tmp_path / "bad.json", count=19))


def test_dataset_creation_is_idempotent(tmp_path):
    cases = load_course_details(_dataset(tmp_path / "full_domain_500.json"))

    class Client:
        def __init__(self):
            self.created = []

        def has_dataset(self, *, dataset_name):
            return False

        def create_dataset(self, dataset_name, **kwargs):
            self.created.append((dataset_name, kwargs))
            return SimpleNamespace(id="dataset-id", name=dataset_name)

        def create_examples(self, **kwargs):
            self.examples = kwargs

    client = Client()
    dataset = ensure_dataset(client, cases)

    assert dataset.name == DATASET_NAME
    assert len(client.examples["examples"]) == 20
    assert client.examples["examples"][0]["inputs"]["case_id"] == "fd-course-001"


def test_stage_scores_keep_route_slots_retrieval_and_citations_separate():
    reference = {
        "expected_route": "course_details",
        "expected_subject": "CS",
        "expected_course_number": "6035",
        "gold_urls": ["https://catalog.gatech.edu/coursesaz/cs/"],
    }
    output = {
        "intent": "course_details",
        "subject": "CS",
        "course_number": "6035",
        "returned_urls": [
            "https://example.gatech.edu/other",
            "https://catalog.gatech.edu/coursesaz/cs",
        ],
        "evidence_valid": True,
        "retry_count": 1,
        "citations": [{"url": "https://catalog.gatech.edu/coursesaz/cs/"}],
        "abstain_reason": None,
    }

    scores = score_stages(output, reference)

    assert scores["route_correct"] is True
    assert scores["slots_correct"] is True
    assert scores["best_gold_rank"] == 2
    assert scores["gold_url_hit_at_5"] is True
    assert scores["gold_url_hit_at_8"] is True
    assert scores["citation_gold_url_hit"] is True
    assert scores["retry_used"] is True

    feedback = stage_evaluator(output, reference)
    by_key = {item["key"]: item["score"] for item in feedback["results"]}
    assert by_key["route_correct"] is True
    assert by_key["gold_url_hit_at_5"] is True
    assert by_key["best_gold_rank"] == 2


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"route_correct": False}, "ROUTING_ERROR"),
        ({"slots_correct": False}, "SLOT_ERROR"),
        ({"gold_in_corpus": False}, "CORPUS_OR_SOURCE_MISSING"),
        ({"best_gold_rank": None, "pre_rerank_gold_rank": 2}, "RERANK_LOSS"),
        ({"best_gold_rank": None}, "RETRIEVAL_MISS"),
        ({"evidence_valid": False}, "EVIDENCE_REJECT"),
        (
            {
                "answer_validation_rejected": True,
                "answer_correct": False,
                "answer_valid": False,
            },
            "ANSWER_VALIDATION_REJECT",
        ),
        ({"answer_correct": False}, "SYNTHESIS_WRONG"),
        ({"answer_valid": False}, "ANSWER_VALIDATION_REJECT"),
        ({}, "PASS"),
    ],
)
def test_failure_stage_assigns_one_primary_reason(overrides, expected):
    result = {
        "route_correct": True,
        "slots_correct": True,
        "gold_in_corpus": True,
        "best_gold_rank": 1,
        "evidence_valid": True,
        "answer_correct": True,
        "answer_valid": True,
        **overrides,
    }

    assert classify_failure(result) == expected


@pytest.mark.asyncio
async def test_semantic_evaluator_keeps_correctness_and_support_separate(monkeypatch):
    monkeypatch.setattr(
        "eval.langsmith.run_course_details.judge_answer",
        lambda case, response: _async_result(
            {"verdict": "CORRECT", "supported": False, "reason": "unsupported"}
        ),
    )
    monkeypatch.setattr(
        "eval.langsmith.run_course_details.get_usage",
        lambda: {"history": [], "total_cost": 0.0},
    )
    inputs = {"case_id": "fd-course-001", "question": "What is CS 6035?"}
    reference = {
        "answer": "Gold",
        "gold_urls": ["https://catalog.gatech.edu/coursesaz/cs/"],
        "gold_sources": ["gt-catalog"],
        "expected_route": "course_details",
        "expected_subject": "CS",
        "expected_course_number": "6035",
    }
    outputs = {
        "intent": "course_details",
        "subject": "CS",
        "course_number": "6035",
        "returned_urls": reference["gold_urls"],
        "evidence_valid": True,
        "answer_valid": True,
        "gold_in_corpus": True,
        "answer": "Answer",
        "citations": [],
    }

    result = await semantic_and_failure_evaluator(inputs, outputs, reference)
    scores = {item["key"]: item.get("score", item.get("value")) for item in result["results"]}

    assert scores["answer_correct"] is True
    assert scores["supported"] is False


async def _async_result(value):
    return value


def test_summary_uses_stage_and_semantic_feedback_without_one_aggregate():
    outputs = {
        "intent": "course_details",
        "subject": "CS",
        "course_number": "6035",
        "returned_urls": ["https://catalog.gatech.edu/coursesaz/cs/"],
        "evidence_valid": True,
        "answer_valid": True,
        "citations": [{"url": "https://catalog.gatech.edu/coursesaz/cs/"}],
        "latency_ms": 20.0,
        "app_usage": {"cost_usd": 0.01},
    }
    reference = {
        "gold_urls": ["https://catalog.gatech.edu/coursesaz/cs/"],
        "expected_route": "course_details",
        "expected_subject": "CS",
        "expected_course_number": "6035",
    }
    row = {
        "run": SimpleNamespace(outputs=outputs, url="https://smith.example/trace"),
        "example": SimpleNamespace(inputs={"case_id": "fd-course-001"}, outputs=reference),
        "evaluation_results": {
            "results": [
                EvaluationResult(key="answer_correct", score=True),
                {"key": "supported", "score": False},
                {"key": "judge_cost_usd", "score": 0.002},
                {"key": "primary_failure_stage", "value": "SYNTHESIS_WRONG"},
            ]
        },
    }

    summary = summarize_rows([row])

    assert summary["answer_correctness"] == 1.0
    assert summary["support_rate"] == 0.0
    assert summary["task_success"] == 0.0
    assert summary["app_cost_usd"] == 0.01
    assert summary["judge_cost_usd"] == 0.002
