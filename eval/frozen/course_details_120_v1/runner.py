from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.usage import get_usage
from app.db.session import AsyncSessionLocal
from app.rag.retrieval import get_text_embeddings
from app.retrieval import CourseDetailsQuery, lookup_course_details
from app.retrieval.documents import DocumentEvidence


@dataclass(frozen=True)
class CourseDetailsCase:
    case_id: str
    question: str
    course_code: str
    gold_answer: str
    gold_urls: tuple[str, ...]
    gold_sources: tuple[str, ...]


def load_cases(path: Path) -> list[CourseDetailsCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for base in payload["base_cases"]:
        course = str(base["expected"]["course_code"])
        values = {
            "original": base["question"],
            "course": course,
            "compact": course.replace(" ", ""),
            "dashed": course.replace(" ", "-"),
        }
        for variant in payload["variant_templates"]:
            cases.append(
                CourseDetailsCase(
                    case_id=f"{base['id']}-{variant['id']}",
                    question=variant["question"].format(**values),
                    course_code=course,
                    gold_answer=str(base["gold_answer"]),
                    gold_urls=tuple(base["gold_urls"]),
                    gold_sources=tuple(base["gold_sources"]),
                )
            )
    if len(cases) != 120 or len({case.case_id for case in cases}) != 120:
        raise ValueError("course-details-120-v1 must contain 120 unique cases")
    return cases


def target_course_rank(course_code: str, evidence: list[DocumentEvidence]) -> int | None:
    subject, number = course_code.split()
    marker = re.compile(rf"\b{re.escape(subject)}\s*-?\s*{re.escape(number)}\b", re.I)
    return next(
        (
            rank
            for rank, item in enumerate(evidence, start=1)
            if marker.search(f"{item.title or ''}\n{item.text}")
        ),
        None,
    )


async def run(path: Path) -> dict[str, object]:
    cases = load_cases(path)
    started = time.perf_counter()
    before = get_usage()
    embeddings = await get_text_embeddings([case.question for case in cases])
    rows = []
    async with AsyncSessionLocal() as session:
        for case, embedding in zip(cases, embeddings, strict=True):
            subject, number = case.course_code.split()
            evidence = await lookup_course_details(
                session, CourseDetailsQuery(subject, number, top_k=5), embedding
            )
            rows.append(
                {"case_id": case.case_id, "rank": target_course_rank(case.course_code, evidence)}
            )
    after = get_usage()
    ranks = [row["rank"] for row in rows]
    return {
        "cases": len(rows),
        "target_course_hit_at_1": sum(rank == 1 for rank in ranks) / len(rows),
        "target_course_hit_at_5": sum(rank is not None for rank in ranks) / len(rows),
        "target_course_mrr_at_5": sum(1 / rank for rank in ranks if rank) / len(rows),
        "misses": [row["case_id"] for row in rows if row["rank"] is None],
        "cost_usd": float(after.get("total_cost", 0)) - float(before.get("total_cost", 0)),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen Course Details retrieval suite")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    summary = asyncio.run(run(parser.parse_args().manifest))
    print(json.dumps(summary, separators=(",", ":")))
    return int(bool(summary["misses"]))


if __name__ == "__main__":
    raise SystemExit(main())
