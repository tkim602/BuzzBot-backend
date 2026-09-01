from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from app.rag.answerer import generate_answer
from eval.langsmith.run_policy_answer import (
    PolicyCase,
    PolicySnapshot,
    _git_sha,
    _records_from_rows,
    answer_case,
    policy_evaluator,
    summarize_records,
)
from langsmith import Client

DATASET_NAME = "buzzbot-calendar-answer-20-v1"
DEFAULT_MANIFEST = Path("eval/frozen/academic_calendar_20_v1/manifest.json")
_EXAMPLE_NAMESPACE = uuid.UUID("b76a9f9f-63a3-489a-a9e1-a9c2d6578ad9")


def load_calendar_snapshot(path: Path) -> PolicySnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = tuple(
        PolicyCase(
            case_id=str(item["id"]),
            question=str(item["question"]),
            gold_answer=str(item["gold_span"]),
            gold_urls=(str(item["gold_url"]),),
            evidence=(
                {
                    "url": str(item["gold_url"]),
                    "source_name": str(item["gold_source"]),
                    "vertical": "calendar",
                    "method": "frozen",
                    "text": str(item["gold_span"]),
                },
            ),
            metadata={"document_hit_at_5": True, "evidence_hit_at_5": True},
        )
        for item in payload["items"]
    )
    if len(cases) != 20 or len({case.case_id for case in cases}) != 20:
        raise ValueError("academic-calendar-20-v1 must contain 20 unique cases")
    return PolicySnapshot(provenance={"manifest": str(path)}, cases=cases)


def ensure_dataset(client: Client, snapshot: PolicySnapshot):
    if client.has_dataset(dataset_name=DATASET_NAME):
        return client.read_dataset(dataset_name=DATASET_NAME)
    dataset = client.create_dataset(
        DATASET_NAME,
        description="Frozen official Calendar events for answer-layer evaluation.",
    )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "id": uuid.uuid5(_EXAMPLE_NAMESPACE, case.case_id),
                "inputs": {
                    "case_id": case.case_id,
                    "question": case.question,
                    "evidence": list(case.evidence),
                    "document_hit": True,
                    "evidence_hit": True,
                },
                "outputs": {
                    "gold_answer": case.gold_answer,
                    "gold_urls": list(case.gold_urls),
                },
            }
            for case in snapshot.cases
        ],
    )
    return dataset


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

        async def calendar_answerer(query, chunks, _intent):
            return await generate_answer(query, chunks, "registrar_calendar")

        return await answer_case(case, answerer=calendar_answerer)

    return target


def write_report(path: Path, summary: dict[str, object], experiment_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Calendar answer frozen-evidence experiment",
                "",
                f"- Generated: {datetime.now(UTC).isoformat()}",
                f"- Git SHA: `{_git_sha()}`",
                f"- Experiment: {experiment_url}",
                f"- Cases: {summary['cases']}",
                f"- Correctness: {float(summary['answer_correctness']):.1%}",
                f"- Support: {float(summary['answer_support']):.1%}",
                f"- Citation entails claim: {float(summary['citation_entails_claim']):.1%}",
                f"- Unsupported confident: {float(summary['unsupported_confident']):.1%}",
                "",
            ]
        ),
        encoding="utf-8",
    )


async def run(manifest: Path, report: Path, json_report: Path) -> dict[str, object]:
    load_dotenv()
    if os.getenv("LANGSMITH_TRACING", "false").lower() != "true":
        raise RuntimeError("set LANGSMITH_TRACING=true for the Calendar answer experiment")
    snapshot = load_calendar_snapshot(manifest)
    client = Client()
    dataset = ensure_dataset(client, snapshot)
    results = await client.aevaluate(
        make_target(),
        data=dataset.name,
        evaluators=[policy_evaluator],
        experiment_prefix="buzzbot-calendar-answer",
        description="Frozen official Calendar evidence; answer layer only.",
        metadata={"git_sha": _git_sha(), "subsystem": "calendar_answer"},
        max_concurrency=0,
    )
    records = _records_from_rows([row async for row in results])
    summary = {**summarize_records(records), "records": records}
    json_report.parent.mkdir(parents=True, exist_ok=True)
    json_report.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_report(report, summary, str(results.url))
    return {key: value for key, value in summary.items() if key != "records"} | {
        "experiment_url": str(results.url)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Calendar answers over frozen evidence")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=Path("docs/evals/calendar_answer_pr10.md"))
    parser.add_argument(
        "--json-report", type=Path, default=Path("eval/quality/calendar_answer_pr10.json")
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.manifest, args.report, args.json_report)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
