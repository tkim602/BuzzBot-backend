from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from eval.langsmith import run_policy_answer
from eval.langsmith.run_policy_answer import (
    TAXONOMY_LABELS,
    answer_case,
    classify_validator,
    deterministic_scores,
    ensure_dataset,
    load_snapshot,
    load_taxonomy,
    make_target,
    policy_evaluator,
    semantic_evaluator,
    summarize_records,
)
from eval.quality.schema import load_manifest_cases

SNAPSHOT = Path("eval/frozen/policy_answer_dev_100_v1/snapshot.json")
TAXONOMY = Path("eval/frozen/policy_answer_dev_100_v1/taxonomy.json")
MANIFEST = Path("eval/quality/manifests/dev_100.json")


def test_policy_snapshot_freezes_exact_dev_100_top_five_evidence():
    snapshot = load_snapshot(SNAPSHOT)
    expected_ids = {case.id for case in load_manifest_cases(MANIFEST)}

    assert snapshot.provenance["retrieval_report_sha256"] == (
        "30813af5e1830f9eb99dc8948bc2e5316e9650f4c6d04aba2b891190ab27da59"
    )
    assert snapshot.provenance["baseline_git_sha"] == ("b6a44bc435bc02202566b44063494146d52ea4c0")
    assert len(snapshot.cases) == 100
    assert {case.case_id for case in snapshot.cases} == expected_ids
    assert all(len(case.evidence) <= 5 for case in snapshot.cases)
    assert all(
        {"url", "source_name", "vertical", "method", "text"} <= set(item)
        for case in snapshot.cases
        for item in case.evidence
    )


def test_policy_taxonomy_covers_exactly_the_twenty_one_answer_layer_failures():
    rows = load_taxonomy(TAXONOMY)

    assert len(rows) == 21
    assert len({row.case_id for row in rows}) == 21
    assert {row.category for row in rows} <= TAXONOMY_LABELS
    assert all(row.rationale.strip() for row in rows)


@pytest.mark.asyncio
async def test_answer_case_uses_frozen_evidence_and_preserves_validation_stages():
    case = load_snapshot(SNAPSHOT).cases[0]
    evidence = {
        "url": case.gold_urls[0],
        "source_name": "official",
        "vertical": "academics",
        "method": "frozen",
        "text": "Recommendations are completely optional.",
    }
    case = replace(case, evidence=(evidence,))

    async def answerer(query, chunks, intent):
        assert query == case.question
        assert intent == "policy"
        assert [chunk.chunk_text for chunk in chunks] == [evidence["text"]]
        return {
            "answer": "Recommendations are completely optional.",
            "citations": [{"url": case.gold_urls[0], "quote": evidence["text"]}],
            "confidence": 1.0,
            "notes": [],
        }

    result = await answer_case(case, answerer=answerer)

    assert result["raw_answer"] == "Recommendations are completely optional."
    assert result["answer"] == result["raw_answer"]
    assert result["grounding_valid"] is True
    assert result["claims_supported"] is True
    assert result["answer_valid"] is True
    assert result["abstained"] is False
    assert result["retrieved_doc_ids"]


@pytest.mark.asyncio
async def test_answer_case_fails_closed_but_keeps_raw_output_for_diagnosis(monkeypatch):
    case = load_snapshot(SNAPSHOT).cases[0]
    evidence = {
        "url": case.gold_urls[0],
        "source_name": "official",
        "vertical": "academics",
        "method": "frozen",
        "text": "Recommendations are completely optional.",
    }
    case = replace(case, evidence=(evidence,))
    monkeypatch.setattr(
        run_policy_answer,
        "check_claim_support",
        AsyncMock(return_value=(True, [])),
    )

    async def answerer(query, chunks, intent):
        return {
            "answer": "Recommendations are required.",
            "citations": [{"url": "https://wrong.example", "quote": "invented"}],
            "confidence": 1.0,
            "notes": [],
        }

    result = await answer_case(case, answerer=answerer)

    assert result["raw_answer"] == "Recommendations are required."
    assert result["answer_valid"] is False
    assert result["abstained"] is True
    assert result["citations"] == []
    assert result["answer"].startswith("I don't have enough official evidence")


def test_policy_metrics_keep_citation_and_abstention_contracts_separate():
    case = load_snapshot(SNAPSHOT).cases[0]
    output = {
        "answer": "Supported answer.",
        "citations": [{"url": case.gold_urls[0], "quote": "Exact evidence."}],
        "confidence": 1.0,
        "abstained": False,
        "answer_valid": True,
    }

    scores = deterministic_scores(case, output, semantic={"supported": False})

    assert scores["citation_present"] is True
    assert scores["citation_source_correct"] is True
    assert scores["output_contract_valid"] is True
    assert scores["abstention_correct"] is True
    assert scores["unsupported_confident"] is True


@pytest.mark.asyncio
async def test_semantic_evaluator_returns_distinct_answer_and_citation_metrics(monkeypatch):
    monkeypatch.setattr(
        run_policy_answer,
        "_call_llm",
        AsyncMock(
            return_value=(
                '{"correct":true,"supported":true,"complete":false,'
                '"citation_entails_claim":true,"abstention_correct":true,'
                '"failure_category":"INCOMPLETE_ANSWER","reason":"missing one item"}'
            )
        ),
    )
    case = load_snapshot(SNAPSHOT).cases[0]

    result = await semantic_evaluator(
        case,
        {
            "answer": "Partial answer.",
            "citations": [{"url": case.gold_urls[0], "quote": "Exact evidence."}],
            "abstained": False,
        },
    )

    assert result["correct"] is True
    assert result["supported"] is True
    assert result["complete"] is False
    assert result["citation_entails_claim"] is True
    assert result["failure_category"] == "INCOMPLETE_ANSWER"


def test_policy_langsmith_dataset_includes_frozen_evidence_inputs():
    snapshot = load_snapshot(SNAPSHOT)

    class Client:
        def has_dataset(self, *, dataset_name):
            return False

        def create_dataset(self, dataset_name, **kwargs):
            self.dataset_name = dataset_name
            self.dataset_kwargs = kwargs
            return SimpleNamespace(id="dataset-id", name=dataset_name)

        def create_examples(self, **kwargs):
            self.examples = kwargs["examples"]

    client = Client()
    dataset = ensure_dataset(client, snapshot)

    assert dataset.name == "buzzbot-policy-answer-dev-100-v1"
    assert len(client.examples) == 100
    assert client.examples[0]["inputs"]["evidence"] == list(snapshot.cases[0].evidence)
    assert client.examples[0]["outputs"]["gold_answer"] == snapshot.cases[0].gold_answer


def test_policy_summary_keeps_all_quality_metrics_separate():
    summary = summarize_records(
        [
            {
                "correct": True,
                "supported": True,
                "citation_present": True,
                "citation_entails_claim": True,
                "citation_source_correct": True,
                "abstention_correct": True,
                "unsupported_confident": False,
                "failure_category": "PASS",
                "validator_outcome": "PASS",
                "cost_usd": 0.01,
            },
            {
                "correct": False,
                "supported": True,
                "citation_present": True,
                "citation_entails_claim": False,
                "citation_source_correct": False,
                "abstention_correct": False,
                "unsupported_confident": False,
                "failure_category": "CITATION_MISMATCH",
                "validator_outcome": "TRUE_REJECTION",
                "cost_usd": 0.02,
            },
        ]
    )

    assert summary["answer_correctness"] == 0.5
    assert summary["answer_support"] == 1.0
    assert summary["citation_present"] == 1.0
    assert summary["citation_entails_claim"] == 0.5
    assert summary["citation_source_correct"] == 0.5
    assert summary["abstention_correct"] == 0.5
    assert summary["unsupported_confident"] == 0.0
    assert summary["failure_categories"] == {"CITATION_MISMATCH": 1, "PASS": 1}
    assert summary["validator_outcomes"] == {"PASS": 1, "TRUE_REJECTION": 1}
    assert summary["cost_usd"] == pytest.approx(0.03)


@pytest.mark.parametrize(
    ("raw", "output", "expected"),
    [
        (
            {"correct": True, "supported": True, "citation_entails_claim": True},
            {"answer_valid": False},
            "FALSE_REJECTION",
        ),
        (
            {"correct": False, "supported": False, "citation_entails_claim": False},
            {"answer_valid": False},
            "TRUE_REJECTION",
        ),
        (
            {"correct": True, "supported": False, "citation_entails_claim": False},
            {"answer_valid": True},
            "MISSED_UNSUPPORTED_CLAIM",
        ),
        (
            {"correct": True, "supported": True, "citation_entails_claim": True},
            {"answer_valid": True},
            "PASS",
        ),
    ],
)
def test_validator_outcome_uses_raw_answer_semantics(raw, output, expected):
    assert classify_validator(raw, output) == expected


@pytest.mark.asyncio
async def test_langsmith_target_uses_the_evidence_stored_in_dataset_inputs(monkeypatch):
    answer = AsyncMock(return_value={"answer": "Frozen answer", "retrieved_doc_ids": ["c1"]})
    monkeypatch.setattr(run_policy_answer, "answer_case", answer)
    case = load_snapshot(SNAPSHOT).cases[0]

    output = await make_target()(
        {
            "case_id": case.case_id,
            "question": case.question,
            "evidence": list(case.evidence[:1]),
        }
    )

    passed_case = answer.await_args.args[0]
    assert passed_case.evidence == case.evidence[:1]
    assert output["answer"] == "Frozen answer"
    assert output["prompt_version"]


@pytest.mark.asyncio
async def test_policy_evaluator_exports_semantic_and_deterministic_feedback(monkeypatch):
    monkeypatch.setattr(
        run_policy_answer,
        "semantic_evaluator",
        AsyncMock(
            return_value={
                "correct": True,
                "supported": True,
                "complete": True,
                "citation_entails_claim": True,
                "abstention_correct": True,
                "failure_category": "PASS",
                "reason": "",
            }
        ),
    )
    monkeypatch.setattr(
        run_policy_answer,
        "get_usage",
        lambda: {"history": [], "total_cost": 0.0},
    )
    case = load_snapshot(SNAPSHOT).cases[0]
    result = await policy_evaluator(
        {"case_id": case.case_id, "question": case.question, "evidence": list(case.evidence)},
        {
            "answer": "Answer",
            "citations": [{"url": case.gold_urls[0], "quote": "Evidence"}],
            "confidence": 1.0,
            "abstained": False,
            "answer_valid": True,
        },
        {"gold_answer": case.gold_answer, "gold_urls": list(case.gold_urls)},
    )
    feedback = {item["key"]: item.get("score", item.get("value")) for item in result["results"]}

    assert feedback["answer_correct"] is True
    assert feedback["answer_supported"] is True
    assert feedback["citation_entails_claim"] is True
    assert feedback["citation_source_correct"] is True
    assert feedback["unsupported_confident"] is False
    assert feedback["failure_category"] == "PASS"
