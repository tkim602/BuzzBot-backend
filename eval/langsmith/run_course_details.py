from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from sqlalchemy import func, select

from app.core.usage import get_usage
from app.graph.state import AgentState
from app.graph.workflow import WorkflowServices, build_workflow
from db.models import Chunk, Document
from db.session import AsyncSessionLocal
from eval.langsmith.datasets import CourseDetailsCase, ensure_dataset, load_course_details
from eval.langsmith.evaluators import score_stages, stage_evaluator
from eval.langsmith.failure_stage import classify_failure
from eval.quality.chat_runner import _usage_delta, judge_answer
from eval.quality.schema import GoldCase
from langsmith import Client

DEFAULT_DATASET = Path(
    "/Users/tkim01/Desktop/personal_project/"
    "buzzbot_full_domain_500_dataset/full_domain_500.json"
)


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _gold_case(
    inputs: dict[str, object], reference_outputs: dict[str, object]
) -> GoldCase:
    return GoldCase(
        id=str(inputs["case_id"]),
        variant_group=str(inputs["case_id"]),
        question=str(inputs["question"]),
        gold_answer=str(reference_outputs["answer"]),
        gold_urls=tuple(str(url) for url in reference_outputs["gold_urls"]),
        gold_sources=tuple(str(source) for source in reference_outputs["gold_sources"]),
        gold_vertical="academics",
        gold_locator=str(reference_outputs["expected_subject"])
        + " "
        + str(reference_outputs["expected_course_number"]),
        question_type="course_detail",
        time_sensitive=False,
        difficulty="frozen",
        style="frozen",
    )


async def semantic_and_failure_evaluator(
    inputs: dict[str, object],
    outputs: dict[str, object],
    reference_outputs: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    before = get_usage()
    if outputs.get("abstain_reason"):
        judged = {"verdict": "ABSTAINED", "supported": False}
    else:
        judged = await judge_answer(_gold_case(inputs, reference_outputs), outputs)
    usage = _usage_delta(before, get_usage())
    correct = judged.get("verdict") == "CORRECT"
    supported = bool(judged.get("supported"))
    scores = {
        **score_stages(outputs, reference_outputs),
        "gold_in_corpus": bool(outputs.get("gold_in_corpus")),
        "answer_correct": correct,
        "supported": supported,
        "answer_valid": bool(outputs.get("answer_valid")),
    }
    return {
        "results": [
            {"key": "answer_correct", "score": correct},
            {"key": "supported", "score": supported},
            {"key": "judge_cost_usd", "score": float(usage.get("cost_usd") or 0.0)},
            {
                "key": "primary_failure_stage",
                "value": classify_failure(scores),
            },
        ]
    }


def make_target(cases: list[CourseDetailsCase]):
    by_id = {case.case_id: case for case in cases}

    async def target(inputs: dict[str, object]) -> dict[str, object]:
        case = by_id[str(inputs["case_id"])]
        before = get_usage()
        started = time.perf_counter()
        async with AsyncSessionLocal() as session:
            graph = build_workflow(WorkflowServices(session))
            result = cast(
                AgentState,
                await graph.ainvoke(
                    {"query": case.question},
                    {
                        "metadata": {
                            "app": "buzzbot",
                            "benchmark": "course-details-20",
                            "case_id": case.case_id,
                            "git_sha": _git_sha(),
                            "environment": "local-eval",
                        },
                        "tags": ["buzzbot", "course-details-20"],
                    },
                ),
            )
            variants = {url for gold in case.gold_urls for url in (gold, gold.rstrip("/"))}
            gold_in_corpus = bool(
                await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .join(Chunk, Chunk.doc_id == Document.doc_id)
                    .where(Document.canonical_url.in_(variants))
                )
            )
        return {
            **result,
            "gold_in_corpus": gold_in_corpus,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "app_usage": _usage_delta(before, get_usage()),
        }

    return target


def _feedback(row: dict[str, object]) -> dict[str, object]:
    evaluation = cast(dict[str, object], row.get("evaluation_results", {}))
    results = cast(list[object], evaluation.get("results", []))
    feedback = {}
    for item in results:
        key = item.get("key") if isinstance(item, dict) else getattr(item, "key", None)
        if key:
            score = item.get("score") if isinstance(item, dict) else getattr(item, "score", None)
            value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
            feedback[str(key)] = score if score is not None else value
    return feedback


def summarize_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    records = []
    for row in rows:
        run = row["run"]
        example = row["example"]
        outputs = cast(dict[str, object], run.outputs or {})
        reference = cast(dict[str, object], example.outputs or {})
        feedback = _feedback(row)
        stages = score_stages(outputs, reference)
        combined = {
            **stages,
            **feedback,
            "gold_in_corpus": bool(outputs.get("gold_in_corpus")),
        }
        records.append(
            {
                "case_id": example.inputs["case_id"],
                **combined,
                "primary_failure_stage": classify_failure(combined),
                "app_cost_usd": float(
                    cast(dict[str, object], outputs.get("app_usage", {})).get("cost_usd") or 0.0
                ),
                "latency_ms": float(outputs.get("latency_ms", 0.0)),
                "trace_url": run.url,
            }
        )
    records.sort(key=lambda record: str(record["case_id"]))

    def rate(key: str) -> float:
        return sum(bool(record.get(key)) for record in records) / len(records) if records else 0.0

    ranks = [int(record["best_gold_rank"]) for record in records if record["best_gold_rank"]]
    latencies = sorted(float(record["latency_ms"]) for record in records)
    p95_index = max(0, int(len(latencies) * 0.95) - 1) if latencies else 0
    return {
        "cases": len(records),
        "task_success": sum(
            bool(
                record.get("answer_correct")
                and record.get("supported")
                and record.get("citation_gold_url_hit")
                and not record.get("abstained")
            )
            for record in records
        )
        / len(records)
        if records
        else 0.0,
        "route_accuracy": rate("route_correct"),
        "slot_accuracy": rate("slots_correct"),
        "gold_url_hit_at_5": rate("gold_url_hit_at_5"),
        "gold_url_hit_at_8": rate("gold_url_hit_at_8"),
        "mrr_at_8": sum(1 / rank for rank in ranks if rank <= 8) / len(records)
        if records
        else 0.0,
        "evidence_valid_rate": rate("evidence_valid"),
        "answer_correctness": rate("answer_correct"),
        "support_rate": rate("supported"),
        "gold_citation_hit": rate("citation_gold_url_hit"),
        "abstention_rate": rate("abstained"),
        "failure_stages": dict(Counter(str(row["primary_failure_stage"]) for row in records)),
        "app_cost_usd": sum(float(row["app_cost_usd"]) for row in records),
        "judge_cost_usd": sum(float(row.get("judge_cost_usd") or 0.0) for row in records),
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": latencies[p95_index] if latencies else 0.0,
        },
        "rows": records,
    }


def write_report(path: Path, summary: dict[str, object], experiment_url: str | None) -> None:
    rows = cast(list[dict[str, object]], summary["rows"])
    lines = [
        "# Course Details LangSmith Baseline",
        "",
        "- Dataset: buzzbot-course-details-20-full-domain-v1",
        f"- Git SHA: `{_git_sha()}`",
        f"- Generated: {datetime.now(UTC).isoformat()}",
        f"- Experiment: {experiment_url or 'unavailable'}",
        f"- Cases: {summary['cases']}",
        f"- Task success: {float(summary['task_success']):.1%}",
        f"- Route accuracy: {float(summary['route_accuracy']):.1%}",
        f"- Slot accuracy: {float(summary['slot_accuracy']):.1%}",
        f"- Gold URL Hit@5: {float(summary['gold_url_hit_at_5']):.1%}",
        f"- Gold URL Hit@8: {float(summary['gold_url_hit_at_8']):.1%}",
        f"- MRR@8: {float(summary['mrr_at_8']):.4f}",
        f"- Evidence valid: {float(summary['evidence_valid_rate']):.1%}",
        f"- Answer correctness: {float(summary['answer_correctness']):.1%}",
        f"- Support: {float(summary['support_rate']):.1%}",
        f"- Gold citation hit: {float(summary['gold_citation_hit']):.1%}",
        f"- Abstention: {float(summary['abstention_rate']):.1%}",
        f"- App cost: ${float(summary['app_cost_usd']):.6f}",
        f"- Judge cost: ${float(summary['judge_cost_usd']):.6f}",
        f"- Latency p50/p95: {summary['latency_ms']['p50']:.1f} / {summary['latency_ms']['p95']:.1f} ms",
        f"- Failure stages: `{json.dumps(summary['failure_stages'], sort_keys=True)}`",
        "- Interpretation: catalog chunks share one canonical subject URL, so URL Hit@5 does not prove that the requested course chunk was retrieved; inspect each linked retrieval span.",
        "",
        "| Case | Route | Gold rank | Evidence | Abstained | Correct | Supported | Stage | Trace |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {case_id} | {route_correct} | {best_gold_rank} | {evidence_valid} | "
            "{abstained} | {answer_correct} | {supported} | {primary_failure_stage} | "
            "[trace]({trace_url}) |".format(**row)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(dataset_path: Path, report_path: Path) -> dict[str, object]:
    load_dotenv()
    if os.getenv("LANGSMITH_TRACING", "false").lower() != "true":
        raise RuntimeError("set LANGSMITH_TRACING=true for the LangSmith baseline")
    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("LANGSMITH_API_KEY is required")

    cases = load_course_details(dataset_path)
    client = Client()
    dataset = ensure_dataset(client, cases)
    results = await client.aevaluate(
        make_target(cases),
        data=dataset.name,
        evaluators=[stage_evaluator, semantic_and_failure_evaluator],
        experiment_prefix="buzzbot-course-details-baseline",
        description="Frozen Course Details 20 baseline; instrumentation only.",
        metadata={"git_sha": _git_sha(), "subsystem": "course_details"},
        max_concurrency=0,
    )
    rows = [row async for row in results]
    summary = summarize_rows(rows)
    write_report(report_path, summary, results.url)
    return {**summary, "experiment_url": results.url}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Course Details 20 LangSmith baseline")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--report", type=Path, default=Path("docs/evals/course_details_langsmith_baseline.md")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = asyncio.run(run(args.dataset, args.report))
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
