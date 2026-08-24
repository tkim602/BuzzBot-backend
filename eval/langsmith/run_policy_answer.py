from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from app.core.config import settings
from app.core.usage import get_usage
from app.rag.answerer import PROMPTS_DIR, _call_llm, _extract_json, generate_answer
from app.rag.grounding import check_binary_polarity, check_claim_support, check_grounding
from app.rag.retrieval import RetrievedChunk
from eval.langsmith.run_course_details import _feedback, _run_url
from eval.quality.chat_runner import _usage_delta
from eval.quality.metrics import normalize_url
from langsmith import Client

TAXONOMY_LABELS = {
    "SYNTHESIS_ERROR",
    "INCOMPLETE_ANSWER",
    "CITATION_MISMATCH",
    "UNSUPPORTED_CLAIM",
    "VALIDATOR_FALSE_REJECTION",
    "UNNECESSARY_ABSTENTION",
    "EVIDENCE_CONFLICT_HANDLING",
    "FORMATTING_CONTRACT_FAILURE",
}
DATASET_NAME = "buzzbot-policy-answer-dev-100-v1"
_EXAMPLE_NAMESPACE = uuid.UUID("632bcaad-1579-4caf-9f95-847089fabfe9")


@dataclass(frozen=True)
class PolicyCase:
    case_id: str
    question: str
    gold_answer: str
    gold_urls: tuple[str, ...]
    evidence: tuple[dict[str, object], ...]
    metadata: dict[str, object]


@dataclass(frozen=True)
class PolicySnapshot:
    provenance: dict[str, str]
    cases: tuple[PolicyCase, ...]


@dataclass(frozen=True)
class TaxonomyRow:
    case_id: str
    category: str
    rationale: str


Answerer = Callable[[str, list[RetrievedChunk], str], Awaitable[dict[str, object]]]
_ABSTENTION = (
    "I don't have enough official evidence to answer that reliably. "
    "Please make the course, term, program, or deadline more specific."
)
_PROMPT_VERSION = hashlib.sha256(
    b"\n".join(
        (PROMPTS_DIR / path).read_bytes() for path in ("chat_system.txt", "chat_user_template.txt")
    )
).hexdigest()[:12]
DEFAULT_SNAPSHOT = Path("eval/frozen/policy_answer_dev_100_v1/snapshot.json")
DEFAULT_TAXONOMY = Path("eval/frozen/policy_answer_dev_100_v1/taxonomy.json")


def load_snapshot(path: Path) -> PolicySnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = tuple(
        PolicyCase(
            case_id=str(item["case_id"]),
            question=str(item["question"]),
            gold_answer=str(item["gold_answer"]),
            gold_urls=tuple(str(url) for url in item["gold_urls"]),
            evidence=tuple(dict(evidence) for evidence in item["evidence"]),
            metadata=dict(item["metadata"]),
        )
        for item in payload["cases"]
    )
    if len(cases) != 100 or len({case.case_id for case in cases}) != 100:
        raise ValueError("policy-answer-dev-100-v1 must contain 100 unique cases")
    return PolicySnapshot(provenance=dict(payload["provenance"]), cases=cases)


def load_taxonomy(path: Path) -> tuple[TaxonomyRow, ...]:
    rows = tuple(
        TaxonomyRow(
            case_id=str(item["case_id"]),
            category=str(item["category"]),
            rationale=str(item["rationale"]),
        )
        for item in json.loads(path.read_text(encoding="utf-8"))["items"]
    )
    if any(row.category not in TAXONOMY_LABELS for row in rows):
        raise ValueError("unknown policy answer taxonomy label")
    return rows


def _chunks(case: PolicyCase) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=hashlib.sha256(f"{item['url']}\n{item['text']}".encode()).hexdigest()[:16],
            url=str(item["url"]),
            title=str(item["source_name"]),
            chunk_text=str(item["text"]),
            score=float(len(case.evidence) - rank),
            source_name=str(item["source_name"]),
            metadata_json={
                "vertical": item["vertical"],
                "retrieval_method": item["method"],
            },
            method=str(item["method"]),
        )
        for rank, item in enumerate(case.evidence)
    ]


async def answer_case(
    case: PolicyCase, *, answerer: Answerer = generate_answer
) -> dict[str, object]:
    chunks = _chunks(case)
    raw = await answerer(case.question, chunks, "policy")
    raw_answer = str(raw.get("answer", ""))
    raw_citations = raw.get("citations", [])
    citations, grounding_notes = check_grounding(
        raw_citations if isinstance(raw_citations, list) else [], chunks
    )
    claims_supported, claim_notes = await check_claim_support(
        raw_answer, chunks, citations=citations
    )
    polarity_consistent, polarity_notes = check_binary_polarity(
        raw_answer, raw.get("_binary_verdict")
    )
    answer_valid = bool(
        citations and claims_supported and polarity_consistent and raw_answer.strip()
    )
    confidence = float(raw.get("confidence", 0.5))
    return {
        "raw_answer": raw_answer,
        "raw_citations": raw_citations,
        "answer": raw_answer if answer_valid else _ABSTENTION,
        "citations": citations if answer_valid else [],
        "confidence": confidence if answer_valid else 0.2,
        "notes": [
            *list(raw.get("notes", [])),
            *grounding_notes,
            *claim_notes,
            *polarity_notes,
        ],
        "grounding_valid": bool(citations),
        "claims_supported": claims_supported,
        "polarity_consistent": polarity_consistent,
        "answer_valid": answer_valid,
        "abstained": not answer_valid,
        "retrieved_doc_ids": [chunk.chunk_id for chunk in chunks],
        "route": "policy",
    }


async def semantic_evaluator(case: PolicyCase, output: dict[str, object]) -> dict[str, object]:
    citations = output.get("citations", [])
    payload = {
        "question": case.question,
        "gold_answer": case.gold_answer,
        "answer": output.get("answer", ""),
        "abstained": bool(output.get("abstained")),
        "citation_quotes": [
            citation.get("quote", "") for citation in citations if isinstance(citation, dict)
        ]
        if isinstance(citations, list)
        else [],
    }
    try:
        parsed = _extract_json(
            await _call_llm(
                (
                    "Evaluate a RAG answer using only the supplied gold answer and citation "
                    "quotes. Return strict JSON with booleans: correct, supported, complete, "
                    "citation_entails_claim, abstention_correct; plus failure_category and a "
                    "short reason. The failure_category must be PASS or one of: "
                    + ", ".join(sorted(TAXONOMY_LABELS))
                    + ". A citation entails a claim only when its quoted text positively "
                    "supports the material answer claim. All cases are answerable, so an "
                    "abstention is incorrect. Use no outside knowledge."
                ),
                json.dumps(payload, ensure_ascii=False),
                temperature=0.0,
                max_tokens=160,
            )
        )
        required = (
            "correct",
            "supported",
            "complete",
            "citation_entails_claim",
            "abstention_correct",
        )
        if any(not isinstance(parsed.get(key), bool) for key in required):
            raise ValueError("malformed semantic evaluation")
        category = str(parsed.get("failure_category", ""))
        if category not in {*TAXONOMY_LABELS, "PASS"}:
            raise ValueError("unknown semantic failure category")
        return {
            **{key: bool(parsed[key]) for key in required},
            "failure_category": category,
            "reason": str(parsed.get("reason", "")),
        }
    except Exception:
        return {
            "correct": False,
            "supported": False,
            "complete": False,
            "citation_entails_claim": False,
            "abstention_correct": False,
            "failure_category": "FORMATTING_CONTRACT_FAILURE",
            "reason": "semantic evaluator failed closed",
        }


def deterministic_scores(
    case: PolicyCase,
    output: dict[str, object],
    *,
    semantic: dict[str, object],
) -> dict[str, bool]:
    citations = output.get("citations", [])
    citation_urls = {
        normalize_url(str(citation.get("url", "")))
        for citation in citations
        if isinstance(citation, dict)
    }
    gold_urls = {normalize_url(url) for url in case.gold_urls}
    abstained = bool(output.get("abstained"))
    supported = bool(semantic.get("supported"))
    return {
        "citation_present": bool(citations),
        "citation_source_correct": bool(citation_urls & gold_urls),
        "output_contract_valid": bool(
            isinstance(output.get("answer"), str)
            and str(output.get("answer", "")).strip()
            and isinstance(citations, list)
            and isinstance(output.get("confidence"), int | float)
            and isinstance(output.get("abstained"), bool)
        ),
        "abstention_correct": not abstained,
        "unsupported_confident": not abstained and not supported,
    }


def classify_validator(
    raw_semantic: dict[str, object], output: dict[str, object]
) -> str:
    raw_safe = bool(
        raw_semantic.get("correct")
        and raw_semantic.get("supported")
        and raw_semantic.get("citation_entails_claim")
    )
    if not output.get("answer_valid"):
        return "FALSE_REJECTION" if raw_safe else "TRUE_REJECTION"
    if not raw_safe:
        return "MISSED_UNSUPPORTED_CLAIM"
    return "PASS"


def _raw_output(output: dict[str, object]) -> dict[str, object]:
    return {
        "answer": output.get("raw_answer", output.get("answer", "")),
        "citations": output.get("raw_citations", output.get("citations", [])),
        "abstained": False,
    }


def _final_semantic(
    raw_semantic: dict[str, object], output: dict[str, object], validator_outcome: str
) -> dict[str, object]:
    if not output.get("abstained"):
        return raw_semantic
    return {
        "correct": False,
        "supported": False,
        "complete": False,
        "citation_entails_claim": False,
        "abstention_correct": False,
        "failure_category": (
            "VALIDATOR_FALSE_REJECTION"
            if validator_outcome == "FALSE_REJECTION"
            else raw_semantic["failure_category"]
        ),
        "reason": raw_semantic["reason"],
    }


def ensure_dataset(client: object, snapshot: PolicySnapshot):
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        count = sum(1 for _ in client.list_examples(dataset_id=dataset.id))
        if count != len(snapshot.cases):
            raise ValueError(
                f"{DATASET_NAME}: expected {len(snapshot.cases)} examples, found {count}"
            )
        return dataset

    dataset = client.create_dataset(
        DATASET_NAME,
        description="Frozen PR9 Policy dev-100 evidence for answer-layer evaluation.",
        metadata={"benchmark_version": "v1", "subsystem": "policy_answer"},
    )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "id": uuid.uuid5(_EXAMPLE_NAMESPACE, f"{DATASET_NAME}:{case.case_id}"),
                "inputs": {
                    "case_id": case.case_id,
                    "question": case.question,
                    "evidence": list(case.evidence),
                    "retrieval_hit": bool(case.metadata["document_hit_at_5"]),
                    "document_hit": bool(case.metadata["document_hit_at_5"]),
                    "evidence_hit": bool(case.metadata["evidence_hit_at_5"]),
                },
                "outputs": {
                    "gold_answer": case.gold_answer,
                    "gold_urls": list(case.gold_urls),
                },
                "metadata": case.metadata,
            }
            for case in snapshot.cases
        ],
    )
    return dataset


def summarize_records(records: list[dict[str, object]]) -> dict[str, object]:
    def rate(rows: list[dict[str, object]], key: str) -> float:
        return sum(bool(record.get(key)) for record in rows) / len(rows) if rows else 0.0

    def quality(rows: list[dict[str, object]]) -> dict[str, float | int]:
        return {
            "cases": len(rows),
            "answer_correctness": rate(rows, "correct"),
            "answer_support": rate(rows, "supported"),
            "citation_entails_claim": rate(rows, "citation_entails_claim"),
            "unsupported_confident": rate(rows, "unsupported_confident"),
        }

    return {
        "cases": len(records),
        "answer_correctness": rate(records, "correct"),
        "answer_support": rate(records, "supported"),
        "citation_present": rate(records, "citation_present"),
        "citation_entails_claim": rate(records, "citation_entails_claim"),
        "citation_source_correct": rate(records, "citation_source_correct"),
        "abstention_correct": rate(records, "abstention_correct"),
        "unsupported_confident": rate(records, "unsupported_confident"),
        "failure_categories": dict(
            sorted(Counter(str(record["failure_category"]) for record in records).items())
        ),
        "validator_outcomes": dict(
            sorted(
                Counter(
                    str(record["validator_outcome"])
                    for record in records
                    if record.get("validator_outcome")
                ).items()
            )
        ),
        "by_evidence_hit": {
            str(value).lower(): quality(
                [record for record in records if bool(record.get("evidence_hit")) is value]
            )
            for value in (False, True)
        },
        "cost_usd": sum(float(record.get("cost_usd", 0.0)) for record in records),
    }


def make_target():
    async def target(inputs: dict[str, object]) -> dict[str, object]:
        case = PolicyCase(
            case_id=str(inputs["case_id"]),
            question=str(inputs["question"]),
            gold_answer="",
            gold_urls=(),
            evidence=tuple(dict(item) for item in inputs["evidence"]),
            metadata={},
        )
        before = get_usage()
        started = time.perf_counter()
        output = await answer_case(case)
        return {
            **output,
            "prompt_version": _PROMPT_VERSION,
            "synthesis_model": settings.openai_model,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "app_usage": _usage_delta(before, get_usage()),
        }

    return target


async def policy_evaluator(
    inputs: dict[str, object],
    outputs: dict[str, object],
    reference_outputs: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    case = PolicyCase(
        case_id=str(inputs["case_id"]),
        question=str(inputs["question"]),
        gold_answer=str(reference_outputs["gold_answer"]),
        gold_urls=tuple(str(url) for url in reference_outputs["gold_urls"]),
        evidence=tuple(dict(item) for item in inputs["evidence"]),
        metadata={},
    )
    before = get_usage()
    raw_semantic = await semantic_evaluator(case, _raw_output(outputs))
    validator_outcome = classify_validator(raw_semantic, outputs)
    semantic = _final_semantic(raw_semantic, outputs, validator_outcome)
    judge_usage = _usage_delta(before, get_usage())
    deterministic = deterministic_scores(case, outputs, semantic=semantic)
    values: dict[str, object] = {
        "answer_correct": semantic["correct"],
        "answer_supported": semantic["supported"],
        "answer_complete": semantic["complete"],
        "citation_entails_claim": semantic["citation_entails_claim"],
        "semantic_abstention_correct": semantic["abstention_correct"],
        "raw_answer_correct": raw_semantic["correct"],
        "raw_answer_supported": raw_semantic["supported"],
        "raw_citation_entails_claim": raw_semantic["citation_entails_claim"],
        **deterministic,
        "judge_cost_usd": float(judge_usage.get("cost_usd") or 0.0),
    }
    return {
        "results": [
            *({"key": key, "score": value} for key, value in values.items()),
            {"key": "failure_category", "value": semantic["failure_category"]},
            {"key": "failure_reason", "value": semantic["reason"]},
            {"key": "validator_outcome", "value": validator_outcome},
        ]
    }


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


async def diagnose(snapshot: PolicySnapshot, rows: tuple[TaxonomyRow, ...]) -> dict[str, object]:
    selected = {row.case_id for row in rows}
    records = []
    for case in snapshot.cases:
        if case.case_id not in selected:
            continue
        before = get_usage()
        output = await answer_case(case)
        raw_semantic = await semantic_evaluator(case, _raw_output(output))
        validator_outcome = classify_validator(raw_semantic, output)
        semantic = _final_semantic(raw_semantic, output, validator_outcome)
        records.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "document_hit": bool(case.metadata["document_hit_at_5"]),
                "evidence_hit": bool(case.metadata["evidence_hit_at_5"]),
                "raw_answer": output["raw_answer"],
                "answer": output["answer"],
                "raw_citations": output["raw_citations"],
                "citations": output["citations"],
                "validator": {
                    key: output[key]
                    for key in (
                        "grounding_valid",
                        "claims_supported",
                        "polarity_consistent",
                        "answer_valid",
                    )
                },
                **semantic,
                "raw_semantic": raw_semantic,
                "validator_outcome": validator_outcome,
                **deterministic_scores(case, output, semantic=semantic),
                "cost_usd": float(_usage_delta(before, get_usage()).get("cost_usd") or 0.0),
            }
        )
    return {**summarize_records(records), "records": records}


def _records_from_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    records = []
    for row in rows:
        run = row["run"]
        example = row["example"]
        outputs = dict(run.outputs or {})
        feedback = _feedback(row)
        records.append(
            {
                "case_id": str(example.inputs["case_id"]),
                "correct": bool(feedback.get("answer_correct")),
                "supported": bool(feedback.get("answer_supported")),
                "citation_present": bool(feedback.get("citation_present")),
                "citation_entails_claim": bool(feedback.get("citation_entails_claim")),
                "citation_source_correct": bool(feedback.get("citation_source_correct")),
                "abstention_correct": bool(feedback.get("abstention_correct")),
                "unsupported_confident": bool(feedback.get("unsupported_confident")),
                "failure_category": str(feedback.get("failure_category", "UNKNOWN")),
                "failure_reason": str(feedback.get("failure_reason", "")),
                "validator_outcome": str(feedback.get("validator_outcome", "UNKNOWN")),
                "document_hit": bool(example.inputs.get("document_hit")),
                "evidence_hit": bool(example.inputs.get("evidence_hit")),
                "raw_answer": str(outputs.get("raw_answer", "")),
                "answer": str(outputs.get("answer", "")),
                "answer_valid": bool(outputs.get("answer_valid")),
                "citations": outputs.get("citations", []),
                "notes": outputs.get("notes", []),
                "grounding_valid": bool(outputs.get("grounding_valid")),
                "claims_supported": bool(outputs.get("claims_supported")),
                "polarity_consistent": bool(outputs.get("polarity_consistent")),
                "retrieved_doc_ids": outputs.get("retrieved_doc_ids", []),
                "cost_usd": float(dict(outputs.get("app_usage", {})).get("cost_usd") or 0.0)
                + float(feedback.get("judge_cost_usd") or 0.0),
                "latency_ms": float(outputs.get("latency_ms", 0.0)),
                "trace_url": _run_url(run),
            }
        )
    return sorted(records, key=lambda record: str(record["case_id"]))


def _write_report(
    report_path: Path,
    json_path: Path,
    summary: dict[str, object],
    experiment_url: str | None,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Policy answer frozen-evidence experiment",
        "",
        f"- Generated: {datetime.now(UTC).isoformat()}",
        f"- Git SHA: `{_git_sha()}`",
        f"- Prompt version: `{_PROMPT_VERSION}`",
        f"- Model: `{settings.openai_model}`",
        f"- Experiment: {experiment_url or 'local diagnostic'}",
        f"- Cases: {summary['cases']}",
        f"- Correctness: {float(summary['answer_correctness']):.1%}",
        f"- Support: {float(summary['answer_support']):.1%}",
        f"- Citation present: {float(summary['citation_present']):.1%}",
        f"- Citation entails claim: {float(summary['citation_entails_claim']):.1%}",
        f"- Citation source correct: {float(summary['citation_source_correct']):.1%}",
        f"- Abstention correct: {float(summary['abstention_correct']):.1%}",
        f"- Unsupported confident: {float(summary['unsupported_confident']):.1%}",
        f"- Cost: ${float(summary['cost_usd']):.6f}",
        f"- Failure categories: `{json.dumps(summary['failure_categories'], sort_keys=True)}`",
        f"- Validator outcomes: `{json.dumps(summary['validator_outcomes'], sort_keys=True)}`",
        f"- Evidence-hit breakdown: `{json.dumps(summary['by_evidence_hit'], sort_keys=True)}`",
        "",
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


async def run_langsmith(
    snapshot_path: Path, report_path: Path, json_path: Path
) -> dict[str, object]:
    load_dotenv()
    if os.getenv("LANGSMITH_TRACING", "false").lower() != "true":
        raise RuntimeError("set LANGSMITH_TRACING=true for the Policy answer experiment")
    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("LANGSMITH_API_KEY is required")
    snapshot = load_snapshot(snapshot_path)
    client = Client()
    dataset = ensure_dataset(client, snapshot)
    results = await client.aevaluate(
        make_target(),
        data=dataset.name,
        evaluators=[policy_evaluator],
        experiment_prefix="buzzbot-policy-answer",
        description="Frozen PR9 evidence; Policy answer/citation/validation only.",
        metadata={
            "git_sha": _git_sha(),
            "subsystem": "policy_answer",
            "prompt_version": _PROMPT_VERSION,
            "synthesis_model": settings.openai_model,
        },
        max_concurrency=0,
    )
    rows = [row async for row in results]
    records = _records_from_rows(rows)
    summary = {**summarize_records(records), "records": records}
    _write_report(report_path, json_path, summary, results.url)
    return {key: value for key, value in summary.items() if key != "records"} | {
        "experiment_url": results.url
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Policy answers over frozen evidence")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--report", type=Path, default=Path("docs/evals/policy_answer_pr10.md"))
    parser.add_argument(
        "--json-report", type=Path, default=Path("eval/quality/policy_answer_pr10.json")
    )
    parser.add_argument("--diagnose-taxonomy", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.diagnose_taxonomy:
        summary = asyncio.run(
            diagnose(load_snapshot(args.snapshot), load_taxonomy(DEFAULT_TAXONOMY))
        )
        _write_report(args.report, args.json_report, summary, None)
        printable = {key: value for key, value in summary.items() if key != "records"}
    else:
        printable = asyncio.run(run_langsmith(args.snapshot, args.report, args.json_report))
    print(json.dumps(printable, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
